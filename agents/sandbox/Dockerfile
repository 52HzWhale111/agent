FROM eclipse-temurin:17-jdk-alpine

# 安装 curl 用于下载依赖
RUN apk add --no-cache curl

WORKDIR /opt/tools

# 下载 JUnit Console Launcher
RUN curl -fL -o junit-platform-console-standalone.jar \
    https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/1.10.2/junit-platform-console-standalone-1.10.2.jar

# 下载 JaCoCo CLI 和 Agent
RUN curl -fL -o jacococli.jar \
    https://repo1.maven.org/maven2/org/jacoco/org.jacoco.cli/0.8.12/org.jacoco.cli-0.8.12-nodeps.jar && \
    curl -fL -o jacocoagent.jar \
    https://repo1.maven.org/maven2/org/jacoco/org.jacoco.agent/0.8.12/org.jacoco.agent-0.8.12-runtime.jar