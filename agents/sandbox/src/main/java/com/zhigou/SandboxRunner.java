package com.zhigou;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

public class SandboxRunner {
    public static void main(String[] args) {
        // 核心修复：强制让 System.out 使用 UTF-8 打印，彻底解决 Windows 终端乱码
        try {
            System.setOut(new PrintStream(System.out, true, "UTF-8"));
        } catch (Exception e) {
            e.printStackTrace();
        }

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

        System.out.println("开始执行沙箱命令...");
        long startTime = System.currentTimeMillis();

        try {
            ProcessBuilder pb = new ProcessBuilder(command);
            pb.redirectErrorStream(true);
            Process process = pb.start();

            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8)
            );
            String line;
            while ((line = reader.readLine()) != null) {
                System.out.println("[容器输出] " + line);
            }

            boolean finished = process.waitFor(10, TimeUnit.SECONDS);
            if (!finished) {
                System.out.println("警告：Java 进程超时，强制终止 Docker 进程！");
                process.destroyForcibly();
            } else {
                long duration = System.currentTimeMillis() - startTime;
                int exitCode = process.exitValue();
                System.out.println("沙箱执行完毕！退出码: " + exitCode + "，耗时: " + duration + " 毫秒");
                if (exitCode == 124) {
                    System.out.println("结论：超时强杀成功（学生代码死循环）");
                } else if (exitCode == 0) {
                    System.out.println("结论：代码正常执行完毕");
                } else {
                    System.out.println("结论：编译或运行出错");
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}