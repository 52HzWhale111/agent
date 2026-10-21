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
import java.util.concurrent.TimeUnit;

public class SandboxServer {
    public static void main(String[] args) throws Exception {
        // 强制设置标准输出和错误输出为 UTF-8，彻底解决乱码
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8));
        System.setErr(new PrintStream(System.err, true, StandardCharsets.UTF_8));

        // 监听 8200 端口
        HttpServer server = HttpServer.create(new InetSocketAddress(8200), 0);
        
        // 路由：POST /sandbox/run
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

                String resultJson = runSandbox();
                sendResponse(exchange, 200, resultJson);

            } catch (Exception e) {
                e.printStackTrace();
            }
        }

        private String runSandbox() {
            String hostDir = "D:/work/agent/agent/agents/sandbox/testcases";
            String[] command = {
                "docker", "run", "--rm",
                "-e", "LANG=C.UTF-8",
                "-v", hostDir + ":/app",
                "-w", "/app",
                "--network", "none",
                "--memory", "256m",
                "--cpus", "1",
                "eclipse-temurin:17-jdk-alpine",
                "sh", "-c", "javac -encoding UTF-8 InfiniteLoop.java && timeout 5 java InfiniteLoop"
            };

            StringBuilder outputBuilder = new StringBuilder();
            long startTime = System.currentTimeMillis();
            int exitCode = -1;

            try {
                ProcessBuilder pb = new ProcessBuilder(command);
                pb.redirectErrorStream(true);
                Process process = pb.start();

                BufferedReader reader = new BufferedReader(
                        new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8)
                );
                String line;
                while ((line = reader.readLine()) != null) {
                    outputBuilder.append(line).append("\\n");
                }

                boolean finished = process.waitFor(10, TimeUnit.SECONDS);
                if (!finished) {
                    process.destroyForcibly();
                    exitCode = 137;
                } else {
                    exitCode = process.exitValue();
                }
            } catch (Exception e) {
                return "{\"status\": \"ERROR\", \"message\": \"沙箱执行异常: " + e.getMessage() + "\"}";
            }

            long duration = System.currentTimeMillis() - startTime;
            String status = (exitCode == 124) ? "TIMEOUT" : (exitCode == 0 ? "SUCCESS" : "FAILED");

            return String.format(
                "{\"status\": \"%s\", \"exitCode\": %d, \"duration\": %d, \"output\": \"%s\"}",
                status, exitCode, duration, outputBuilder.toString().replace("\"", "\\\"").replace("\n", "\\n")
            );
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