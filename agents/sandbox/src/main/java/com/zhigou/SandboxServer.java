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
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

public class SandboxServer {
    public static void main(String[] args) throws Exception {
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8));
        System.setErr(new PrintStream(System.err, true, StandardCharsets.UTF_8));

        HttpServer server = HttpServer.create(new InetSocketAddress(8200), 0);
        server.createContext("/sandbox/run-tests", new SandboxHandler());
        server.setExecutor(null);
        System.out.println("JUnit 沙箱服务已启动，正在监听 8200 端口...");
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

                String requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                String[] parts = requestBody.split("---TEST---");
                if (parts.length != 2) {
                    sendResponse(exchange, 400, "{\"status\": \"ERROR\", \"message\": \"请使用 ---TEST--- 分隔代码\"}");
                    return;
                }

                String resultJson = runJUnitSandbox(parts[0].trim(), parts[1].trim());
                sendResponse(exchange, 200, resultJson);

            } catch (Exception e) {
                e.printStackTrace();
            }
        }

        private String runJUnitSandbox(String studentCode, String testCode) {
            Path taskDir = null;
            try {
                Path tmpBaseDir = Paths.get("D:/work/agent/agent/agents/sandbox/tmp");
                Files.createDirectories(tmpBaseDir);
                taskDir = Files.createDirectory(tmpBaseDir.resolve(UUID.randomUUID().toString()));

                // 自动提取类名（默认 Main 和 MainTest）
                String studentClass = extractClassName(studentCode, "Main");
                String testClass = extractClassName(testCode, studentClass + "Test");

                // 按正确的类名保存文件
                Files.writeString(taskDir.resolve(studentClass + ".java"), studentCode, StandardCharsets.UTF_8);
                Files.writeString(taskDir.resolve(testClass + ".java"), testCode, StandardCharsets.UTF_8);

                String hostDir = taskDir.toAbsolutePath().toString().replace("\\", "/");

                String shellScript = 
                    "mkdir -p classes && " +
                    "javac -encoding UTF-8 -d classes -cp /opt/tools/junit-platform-console-standalone.jar *.java && " +
                    "java -javaagent:/opt/tools/jacocoagent.jar=destfile=jacoco.exec " +
                    "-jar /opt/tools/junit-platform-console-standalone.jar --class-path classes --scan-class-path ; " +
                    "java -jar /opt/tools/jacococli.jar report jacoco.exec --classfiles classes --sourcefiles . --xml coverage.xml";

                String[] command = {
                    "docker", "run", "--rm",
                    "-e", "LANG=C.UTF-8",
                    "-v", hostDir + ":/app",
                    "-w", "/app",
                    "--network", "none",
                    "--memory", "256m",
                    "--cpus", "1",
                    "zhigou-sandbox-jdk17:v1",
                    "sh", "-c", shellScript
                };

                ProcessBuilder pb = new ProcessBuilder(command);
                pb.redirectErrorStream(true);
                Process process = pb.start();

                StringBuilder consoleOutput = new StringBuilder();
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        consoleOutput.append(line).append("\n");
                    }
                }

                process.waitFor(60, TimeUnit.SECONDS);

                String consoleStr = consoleOutput.toString();
                int testsSuccess = extractNumber(consoleStr, "\\[ *(\\d+) tests successful *\\]");
                int testsFailed = extractNumber(consoleStr, "\\[ *(\\d+) tests failed *\\]");
                
                double lineCoverage = 0.0;
                Path xmlPath = taskDir.resolve("coverage.xml");
                if (Files.exists(xmlPath)) {
                    String xml = Files.readString(xmlPath);
                    Pattern p = Pattern.compile("<counter type=\"LINE\" missed=\"(\\d+)\" covered=\"(\\d+)\"/>");
                    Matcher m = p.matcher(xml);
                    if (m.find()) {
                        int missed = Integer.parseInt(m.group(1));
                        int covered = Integer.parseInt(m.group(2));
                        if (missed + covered > 0) {
                            lineCoverage = Math.round((double) covered / (missed + covered) * 1000.0) / 10.0;
                        }
                    }
                }

                // 智能状态判定
                String status;
                if (consoleStr.contains(" error: ")) {
                    status = "COMPILE_ERROR"; // 编译报错
                } else if (testsFailed > 0) {
                    status = "FAILED"; // 测试没通过
                } else if (testsSuccess > 0) {
                    status = "SUCCESS"; // 测试全过
                } else {
                    status = "NO_TESTS"; // 没找到测试用例
                }

                return String.format(
                    "{\"status\": \"%s\", \"tests\": {\"success\": %d, \"failed\": %d}, \"coverage\": {\"line\": %.1f}, \"output\": \"%s\"}",
                    status, testsSuccess, testsFailed, lineCoverage, 
                    consoleStr.replace("\"", "\\\"").replace("\n", "\\n")
                );

            } catch (Exception e) {
                return "{\"status\": \"ERROR\", \"message\": \"" + e.getMessage().replace("\"", "\\\"") + "\"}";
            } finally {
                if (taskDir != null) {
                    try (Stream<Path> walk = Files.walk(taskDir)) {
                        walk.sorted(Comparator.reverseOrder())
                            .forEach(p -> { try { Files.deleteIfExists(p); } catch (Exception ignored) {} });
                    } catch (Exception ignored) {}
                }
            }
        }

        private String extractClassName(String code, String defaultName) {
            Matcher m = Pattern.compile("public\\s+class\\s+(\\w+)").matcher(code);
            return m.find() ? m.group(1) : defaultName;
        }

        private int extractNumber(String text, String regex) {
            Matcher m = Pattern.compile(regex).matcher(text);
            return m.find() ? Integer.parseInt(m.group(1)) : 0;
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