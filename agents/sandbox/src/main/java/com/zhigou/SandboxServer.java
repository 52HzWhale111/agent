package com.zhigou;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PrintStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Comparator;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;

public class SandboxServer {
    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8));
        System.setErr(new PrintStream(System.err, true, StandardCharsets.UTF_8));

        HttpServer server = HttpServer.create(new InetSocketAddress(8200), 0);
        server.createContext("/sandbox/run", new SandboxHandler());
        server.setExecutor(null);
        System.out.println("沙箱服务已启动，正在监听 8200 端口...");
        server.start();
    }

    static class SandboxHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) {
            try {
                exchange.getResponseHeaders().add("Access-Control-Allow-Origin", "*");
                exchange.getResponseHeaders().add("Access-Control-Allow-Methods", "POST, OPTIONS");
                exchange.getResponseHeaders().add("Content-Type", "application/json; charset=UTF-8");

                if ("OPTIONS".equalsIgnoreCase(exchange.getRequestMethod())) {
                    exchange.sendResponseHeaders(204, -1);
                    return;
                }

                if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                    sendResponse(exchange, 405, "{\"error\": \"Method Not Allowed\"}");
                    return;
                }

                // 1. 读取请求体中的学生代码
                String studentCode = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                if (studentCode.trim().isEmpty()) {
                    sendResponse(exchange, 400, "{\"error\": \"代码不能为空\"}");
                    return;
                }

                // 2. 执行沙箱逻辑
                String resultJson = runSandbox(studentCode);
                sendResponse(exchange, 200, resultJson);

            } catch (Exception e) {
                e.printStackTrace();
            }
        }

        private String runSandbox(String studentCode) {
            // 创建临时目录（在项目的 tmp 目录下）
            Path tmpBaseDir = Paths.get("D:/work/agent/agent/agents/sandbox/tmp");
            Path taskDir = null;
            try {
                Files.createDirectories(tmpBaseDir);
                taskDir = Files.createDirectory(tmpBaseDir.resolve(UUID.randomUUID().toString()));
                
                // 将接收到的代码写入 Main.java
                Path javaFile = taskDir.resolve("Main.java");
                Files.writeString(javaFile, studentCode, StandardCharsets.UTF_8);

                // 将 Windows 路径转为 Docker 挂载路径格式
                String hostDir = taskDir.toAbsolutePath().toString().replace("\\", "/");

                String[] command = {
                    "docker", "run", "--rm",
                    "-e", "LANG=C.UTF-8",
                    "-v", hostDir + ":/app",
                    "-w", "/app",
                    "--network", "none",
                    "--memory", "256m",
                    "--cpus", "1",
                    "eclipse-temurin:17-jdk-alpine",
                    "sh", "-c", "javac -encoding UTF-8 Main.java && timeout 5 java Main"
                };

                StringBuilder outputBuilder = new StringBuilder();
                long startTime = System.currentTimeMillis();
                int exitCode = -1;

                ProcessBuilder pb = new ProcessBuilder(command);
                pb.redirectErrorStream(true);
                Process process = pb.start();

                BufferedReader reader = new BufferedReader(
                        new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8)
                );
                String line;
                while ((line = reader.readLine()) != null) {
                    outputBuilder.append(line).append("\n");
                }

                boolean finished = process.waitFor(10, TimeUnit.SECONDS);
                if (!finished) {
                    process.destroyForcibly();
                    exitCode = 137; 
                } else {
                    exitCode = process.exitValue();
                }

                long duration = System.currentTimeMillis() - startTime;
                String status = (exitCode == 124) ? "TIMEOUT" : (exitCode == 0 ? "SUCCESS" : "FAILED");

                return String.format(
                    "{\"status\": \"%s\", \"exitCode\": %d, \"duration\": %d, \"output\": \"%s\"}",
                    status, exitCode, duration, outputBuilder.toString().replace("\"", "\\\"").replace("\n", "\\n")
                );

            } catch (Exception e) {
                return "{\"status\": \"ERROR\", \"message\": \"沙箱执行异常: " + e.getMessage().replace("\"", "\\\"") + "\"}";
            } finally {
                // 3. 无论成功还是失败，最后都要清理临时目录
                if (taskDir != null) {
                    try (Stream<Path> walk = Files.walk(taskDir)) {
                        walk.sorted(Comparator.reverseOrder())
                            .forEach(p -> {
                                try { Files.deleteIfExists(p); } catch (Exception ignored) {}
                            });
                    } catch (Exception ignored) {}
                }
            }
        }

        private void sendResponse(HttpExchange exchange, int statusCode, String response) throws Exception {
            byte[] responseBytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(statusCode, responseBytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(responseBytes);
            }
        }
    }
}