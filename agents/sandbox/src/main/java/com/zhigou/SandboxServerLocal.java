package com.zhigou;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.InputStreamReader;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Local integration entry point; keeps the original engineering delivery intact. */
public class SandboxServerLocal {
    static final int OUTPUT_LIMIT = 65536;

    public static void main(String[] args) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 8200), 0);
        server.createContext("/sandbox/run-tests", SandboxServerLocal::handle);
        server.start();
        System.out.println("Real Docker/JUnit sandbox: http://127.0.0.1:8200/sandbox/run-tests");
    }

    static void handle(HttpExchange exchange) throws java.io.IOException {
        try {
            if (exchange.getRequestMethod().equals("OPTIONS")) {
                exchange.getResponseHeaders().set("Allow", "POST, OPTIONS");
                exchange.sendResponseHeaders(204, -1);
                return;
            }
            if (!exchange.getRequestMethod().equals("POST")) {
                respond(exchange, 405, "{\"status\":\"ERROR\",\"message\":\"Use POST\"}");
                return;
            }
            byte[] body = exchange.getRequestBody().readNBytes(1024 * 1024 + 1);
            if (body.length > 1024 * 1024) {
                respond(exchange, 413, "{\"status\":\"ERROR\",\"message\":\"Request too large\"}");
                return;
            }
            String[] parts = new String(body, StandardCharsets.UTF_8).split("---TEST---", -1);
            if (parts.length != 2 || parts[0].isBlank() || parts[1].isBlank()) {
                respond(exchange, 400, "{\"status\":\"ERROR\",\"message\":\"Provide code and tests separated by ---TEST---\"}");
                return;
            }
            respond(exchange, 200, run(parts[0].trim(), parts[1].trim()));
        } finally {
            exchange.close();
        }
    }

    static String run(String code, String tests) {
        Path task = null;
        Process process = null;
        String name = "zhigou-" + UUID.randomUUID();
        try {
            Path base = Path.of(System.getenv().getOrDefault("SANDBOX_TMP_DIR", "tmp")).toAbsolutePath();
            Files.createDirectories(base);
            task = Files.createTempDirectory(base, "task-");
            String studentClass = className(code, "Main");
            String testClass = className(tests, studentClass + "Test");
            if (studentClass.equals(testClass)) {
                return result("COMPILE_ERROR", 0, 0, 0, "Student and test classes must have different names");
            }
            Files.writeString(task.resolve(studentClass + ".java"), code, StandardCharsets.UTF_8);
            Files.writeString(task.resolve(testClass + ".java"), tests, StandardCharsets.UTF_8);
            // Separate class directories keep JUnit test code out of student coverage.
            String script = "mkdir -p classes test-classes; "
                + "javac -encoding UTF-8 -d classes '" + studentClass + ".java' || exit 10; "
                + "javac -encoding UTF-8 -d test-classes -cp classes:/opt/tools/junit-platform-console-standalone.jar '" + testClass + ".java' || exit 10; "
                // A fixed session ID avoids JaCoCo's hostname DNS lookup in a networkless container.
                + "timeout -s KILL 5 java '-javaagent:/opt/tools/jacocoagent.jar=destfile=jacoco.exec,sessionid=" + name
                + ",excludes=org.junit.*:org.apiguardian.*:org.opentest4j.*' "
                + "-jar /opt/tools/junit-platform-console-standalone.jar execute --disable-ansi-colors --details=summary "
                + "--class-path classes:test-classes --scan-class-path --fail-if-no-tests; test_exit=$?; "
                + "if [ $test_exit -eq 124 ] || [ $test_exit -eq 137 ]; then exit 124; fi; "
                + "java -jar /opt/tools/jacococli.jar report jacoco.exec --classfiles classes --sourcefiles . --xml coverage.xml || exit 20; "
                + "exit $test_exit";
            process = docker("run", "--rm", "--pull=never", "--name", name,
                "--label", "zhigou.sandbox=local", "--init", "--network", "none", "--memory", "256m",
                "--cpus", "1", "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--read-only", "--tmpfs", "/tmp:rw,nosuid,noexec,size=64m", "-e", "LANG=C.UTF-8",
                "--mount", "type=bind,source=" + dockerPath(task) + ",target=/app", "-w", "/app",
                "zhigou-sandbox-jdk17:v1", "sh", "-c", script).redirectErrorStream(true).start();
            StringBuffer output = new StringBuffer();
            Process active = process;
            Thread reader = new Thread(() -> {
                try (var input = new InputStreamReader(active.getInputStream(), StandardCharsets.UTF_8)) {
                    char[] buffer = new char[4096];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        synchronized (output) {
                            output.append(buffer, 0, count);
                            if (output.length() > OUTPUT_LIMIT) output.delete(0, output.length() - OUTPUT_LIMIT);
                        }
                    }
                } catch (java.io.IOException ignored) { }
            });
            reader.setDaemon(true);
            reader.start();
            boolean finished = process.waitFor(60, TimeUnit.SECONDS);
            if (!finished) {
                removeContainer(name);
                process.destroyForcibly();
            }
            reader.join(2000);
            String console = output.toString();
            int exit = finished ? process.exitValue() : 124;
            int success = number(console, "\\[\\s*(\\d+) tests successful\\s*\\]");
            int failed = number(console, "\\[\\s*(\\d+) tests failed\\s*\\]");
            String status = switch (exit) {
                case 124 -> "TIMEOUT";
                case 10 -> "COMPILE_ERROR";
                case 0 -> success > 0 ? "SUCCESS" : "NO_TESTS";
                case 1 -> failed > 0 ? "FAILED" : "ERROR";
                case 2 -> "NO_TESTS";
                default -> "ERROR";
            };
            double coverage = 0;
            Path xml = task.resolve("coverage.xml");
            if (Files.exists(xml)) {
                Matcher counter = Pattern.compile("<counter type=\"LINE\" missed=\"(\\d+)\" covered=\"(\\d+)\"/>").matcher(Files.readString(xml));
                // The final LINE counter is the report total, rather than the first method.
                while (counter.find()) {
                    int missed = Integer.parseInt(counter.group(1)), covered = Integer.parseInt(counter.group(2));
                    coverage = missed + covered == 0 ? 0 : covered * 100.0 / (missed + covered);
                }
            }
            return result(status, success, failed, coverage, console);
        } catch (Exception e) {
            return result("ERROR", 0, 0, 0, e.toString());
        } finally {
            removeContainer(name);
            if (process != null && process.isAlive()) process.destroyForcibly();
            if (task != null) {
                try (var files = Files.walk(task)) {
                    for (Path file : files.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(file);
                } catch (java.io.IOException e) {
                    System.err.println("Unable to clean sandbox task: " + e.getMessage());
                }
            }
        }
    }

    static void removeContainer(String name) {
        try {
            Process cleanup = docker("rm", "-f", name)
                .redirectOutput(ProcessBuilder.Redirect.DISCARD).redirectError(ProcessBuilder.Redirect.DISCARD).start();
            if (!cleanup.waitFor(10, TimeUnit.SECONDS)) cleanup.destroyForcibly();
        } catch (Exception e) {
            System.err.println("Unable to confirm container cleanup: " + e.getMessage());
        }
    }

    static ProcessBuilder docker(String... arguments) {
        var command = new ArrayList<String>();
        String distro = System.getenv("SANDBOX_WSL_DISTRO");
        if (distro != null && !distro.isBlank()) {
            command.addAll(Arrays.asList("wsl.exe", "--distribution", distro, "--user", "root", "--exec", "docker"));
        } else {
            command.add("docker");
        }
        command.addAll(Arrays.asList(arguments));
        return new ProcessBuilder(command);
    }

    static String dockerPath(Path path) throws Exception {
        String distro = System.getenv("SANDBOX_WSL_DISTRO");
        if (distro == null || distro.isBlank()) return path.toString();
        Process conversion = new ProcessBuilder("wsl.exe", "--distribution", distro, "--user", "root", "--exec",
            "wslpath", "-a", "-u", path.toString()).start();
        if (!conversion.waitFor(10, TimeUnit.SECONDS)) {
            conversion.destroyForcibly();
            throw new java.io.IOException("WSL path conversion timed out");
        }
        String converted = new String(conversion.getInputStream().readAllBytes(), StandardCharsets.UTF_8).trim();
        if (conversion.exitValue() != 0 || !converted.startsWith("/")) throw new java.io.IOException("WSL path conversion failed");
        return converted;
    }

    static String className(String code, String fallback) {
        Matcher match = Pattern.compile("public\\s+class\\s+([A-Za-z_$][A-Za-z0-9_$]*)").matcher(code);
        return match.find() ? match.group(1) : fallback;
    }

    static int number(String text, String regex) {
        Matcher match = Pattern.compile(regex).matcher(text);
        int count = 0;
        while (match.find()) count = Integer.parseInt(match.group(1));
        return count;
    }

    static String result(String status, int success, int failed, double line, String output) {
        return String.format(Locale.ROOT,
            "{\"status\":\"%s\",\"tests\":{\"success\":%d,\"failed\":%d},\"coverage\":{\"line\":%.1f},\"output\":%s}",
            status, success, failed, line, quote(output));
    }

    static String quote(String value) {
        StringBuilder json = new StringBuilder("\"");
        for (char ch : value.toCharArray()) {
            if (ch == '"' || ch == '\\') json.append('\\').append(ch);
            else if (ch < 32) json.append(String.format("\\u%04x", (int) ch));
            else json.append(ch);
        }
        return json.append('"').toString();
    }

    static void respond(HttpExchange exchange, int status, String json) throws java.io.IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }
}
