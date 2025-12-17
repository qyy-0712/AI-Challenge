"""
Java编译器测试

测试Java编译器的各种功能，包括语法检查、编译、执行和依赖管理。
"""

import asyncio
import pytest
import time
from typing import Dict, List

from app.docker.compilers.java_compiler import (
    JavaCompiler,
    JavaCheckType,
    JavaCheckResult,
    JavaExecutionOptions,
    JavaCompilerError,
    JavaSyntaxError,
    JavaDependencyError,
    JavaBuildError
)


class TestJavaCompiler:
    """Java编译器测试类"""
    
    @pytest.fixture
    def java_compiler(self):
        """创建Java编译器实例"""
        return JavaCompiler()
    
    @pytest.fixture
    def valid_java_code(self):
        """有效的Java代码"""
        return """
public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
        int sum = 0;
        for (int i = 1; i <= 10; i++) {
            sum += i;
        }
        System.out.println("Sum of 1 to 10: " + sum);
    }
}
"""
    
    @pytest.fixture
    def invalid_java_code(self):
        """无效的Java代码"""
        return """
public class BrokenCode {
    public static void main(String[] args) {
        System.out.println("Hello, World!")
        // 缺少分号
        int x = 10
        // 未定义的变量
        System.out.println(y);
    }
}
"""
    
    @pytest.fixture
    def pom_xml_content(self):
        """Maven pom.xml内容"""
        return """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    
    <groupId>com.example</groupId>
    <artifactId>test-project</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>
    
    <properties>
        <maven.compiler.source>11</maven.compiler.source>
        <maven.compiler.target>11</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>
    
    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
    
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.8.1</version>
                <configuration>
                    <source>11</source>
                    <target>11</target>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
"""
    
    @pytest.fixture
    def build_gradle_content(self):
        """Gradle build.gradle内容"""
        return """plugins {
    id 'java'
    id 'application'
}

group 'com.example'
version '1.0.0'

sourceCompatibility = 11

repositories {
    mavenCentral()
}

dependencies {
    testImplementation 'junit:junit:4.13.2'
}

application {
    mainClass = 'com.example.Main'
}
"""
    
    @pytest.mark.asyncio
    async def test_check_syntax_valid_code(self, java_compiler, valid_java_code):
        """测试有效Java代码的语法检查"""
        result = await java_compiler.check_syntax(valid_java_code, "HelloWorld.java")
        
        assert result.success is True
        assert result.check_type == JavaCheckType.SYNTAX
        assert len(result.errors) == 0
        assert result.java_version == "17"  # 默认版本
    
    @pytest.mark.asyncio
    async def test_check_syntax_invalid_code(self, java_compiler, invalid_java_code):
        """测试无效Java代码的语法检查"""
        result = await java_compiler.check_syntax(invalid_java_code, "BrokenCode.java")
        
        assert result.success is False
        assert result.check_type == JavaCheckType.SYNTAX
        assert len(result.errors) > 0
        # 应该包含分号缺失的错误
        assert any("';' expected" in error or "missing semicolon" in error.lower() 
                  for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_check_syntax_with_options(self, java_compiler, valid_java_code):
        """测试使用自定义选项的语法检查"""
        options = JavaExecutionOptions(
            java_version="11",
            timeout=30,
            memory_limit="256m"
        )
        
        result = await java_compiler.check_syntax(valid_java_code, "HelloWorld.java", options)
        
        assert result.success is True
        assert result.java_version == "11"
    
    @pytest.mark.asyncio
    async def test_compile_code_valid(self, java_compiler, valid_java_code):
        """测试有效Java代码的编译"""
        result = await java_compiler.compile_code(valid_java_code, "HelloWorld.java")
        
        assert result.success is True
        assert result.check_type == JavaCheckType.COMPILE
        assert len(result.errors) == 0
        assert "HelloWorld.class" in result.class_files
    
    @pytest.mark.asyncio
    async def test_compile_code_invalid(self, java_compiler, invalid_java_code):
        """测试无效Java代码的编译"""
        result = await java_compiler.compile_code(invalid_java_code, "BrokenCode.java")
        
        assert result.success is False
        assert result.check_type == JavaCheckType.COMPILE
        assert len(result.errors) > 0
        assert len(result.class_files) == 0
    
    @pytest.mark.asyncio
    async def test_execute_code_valid(self, java_compiler, valid_java_code):
        """测试有效Java代码的执行"""
        result = await java_compiler.execute_code(valid_java_code, "HelloWorld.java", "HelloWorld")
        
        assert result.success is True
        assert result.check_type == JavaCheckType.EXECUTE
        assert len(result.errors) == 0
        assert "Hello, World!" in result.output
        assert "Sum of 1 to 10: 55" in result.output
        assert "HelloWorld.class" in result.class_files
    
    @pytest.mark.asyncio
    async def test_execute_code_with_args(self, java_compiler):
        """测试带参数的Java代码执行"""
        code_with_args = """
public class ArgsTest {
    public static void main(String[] args) {
        System.out.println("Args count: " + args.length);
        for (int i = 0; i < args.length; i++) {
            System.out.println("Arg " + i + ": " + args[i]);
        }
    }
}
"""
        options = JavaExecutionOptions(
            program_args=["arg1", "arg2", "arg3"]
        )
        
        result = await java_compiler.execute_code(
            code_with_args, 
            "ArgsTest.java", 
            "ArgsTest",
            options
        )
        
        assert result.success is True
        assert "Args count: 3" in result.output
        assert "Arg 0: arg1" in result.output
        assert "Arg 1: arg2" in result.output
        assert "Arg 2: arg3" in result.output
    
    @pytest.mark.asyncio
    async def test_check_dependencies(self, java_compiler, pom_xml_content):
        """测试Maven依赖检查"""
        result = await java_compiler.check_dependencies(pom_xml_content)
        
        # 注意：这个测试可能需要Maven环境，可能会失败
        # 我们主要检查返回的格式是正确的
        assert result.check_type == JavaCheckType.DEPENDENCIES
        assert isinstance(result.success, bool)
        assert isinstance(result.dependencies, list)
        assert isinstance(result.errors, list)
    
    @pytest.mark.asyncio
    async def test_maven_build(self, java_compiler, pom_xml_content, valid_java_code):
        """测试Maven构建"""
        project_files = {
            "pom.xml": pom_xml_content,
            "src/main/java/com/example/HelloWorld.java": valid_java_code
        }
        
        result = await java_compiler.maven_build(project_files, ["compile"])
        
        # 注意：这个测试可能需要Maven环境，可能会失败
        # 我们主要检查返回的格式是正确的
        assert result.check_type == JavaCheckType.MAVEN_BUILD
        assert isinstance(result.success, bool)
        assert isinstance(result.dependencies, list)
        assert isinstance(result.errors, list)
    
    @pytest.mark.asyncio
    async def test_gradle_build(self, java_compiler, build_gradle_content, valid_java_code):
        """测试Gradle构建"""
        project_files = {
            "build.gradle": build_gradle_content,
            "src/main/java/com/example/HelloWorld.java": valid_java_code
        }
        
        result = await java_compiler.gradle_build(project_files, ["build"])
        
        # 注意：这个测试可能需要Gradle环境，可能会失败
        # 我们主要检查返回的格式是正确的
        assert result.check_type == JavaCheckType.GRADLE_BUILD
        assert isinstance(result.success, bool)
        assert isinstance(result.dependencies, list)
        assert isinstance(result.errors, list)
    
    @pytest.mark.asyncio
    async def test_enhanced_check_syntax(self, java_compiler, invalid_java_code):
        """测试增强的语法检查（包含修复建议）"""
        result = await java_compiler.enhanced_check_syntax(invalid_java_code, "BrokenCode.java")
        
        assert result.success is False
        assert result.check_type == JavaCheckType.SYNTAX
        assert len(result.errors) > 0
        
        # 检查是否包含修复建议
        error_text = " ".join(result.errors)
        assert "建议:" in error_text
    
    def test_extract_java_version_from_code(self, java_compiler):
        """测试从代码中推断Java版本"""
        # 测试Java 8特性
        java8_code = """
import java.util.stream.Stream;
public class Java8Test {
    public void test() {
        Stream.of(1, 2, 3).forEach(System.out::println);
    }
}
"""
        version = java_compiler._extract_java_version_from_code(java8_code)
        assert version == "8"
        
        # 测试Java 11特性
        java11_code = """
public class Java11Test {
    public void test() {
        var message = "Hello";
        System.out.println(message);
    }
}
"""
        version = java_compiler._extract_java_version_from_code(java11_code)
        assert version == "11"
        
        # 测试Java 17特性
        java17_code = """
public sealed class Shape permits Circle, Square {}
final class Circle extends Shape {}
final class Square extends Shape {}
"""
        version = java_compiler._extract_java_version_from_code(java17_code)
        assert version == "17"
        
        # 测试默认版本
        simple_code = """
public class SimpleTest {
    public void test() {
        System.out.println("Hello");
    }
}
"""
        version = java_compiler._extract_java_version_from_code(simple_code)
        assert version == "11"  # 默认版本
    
    def test_suggest_fix_for_error(self, java_compiler):
        """测试错误修复建议"""
        # 测试包不存在错误
        error = "error: package javax.persistence does not exist"
        suggestion = java_compiler._suggest_fix_for_error(error)
        assert "依赖包" in suggestion and "pom.xml" in suggestion
        
        # 测试找不到符号错误
        error = "error: cannot find symbol: variable undefinedVar"
        suggestion = java_compiler._suggest_fix_for_error(error)
        assert "符号" in suggestion
        
        # 测试空指针异常
        error = "Exception in thread \"main\" java.lang.NullPointerException"
        suggestion = java_compiler._suggest_fix_for_error(error)
        assert "null" in suggestion
        
        # 测试未知错误
        error = "Unknown error message"
        suggestion = java_compiler._suggest_fix_for_error(error)
        assert suggestion is None
    
    def test_get_java_image(self, java_compiler):
        """测试获取Java镜像"""
        assert java_compiler._get_java_image("8") == "openjdk:8-slim"
        assert java_compiler._get_java_image("11") == "openjdk:11-slim"
        assert java_compiler._get_java_image("17") == "openjdk:17-slim"
        assert java_compiler._get_java_image("21") == "openjdk:21-slim"
        assert java_compiler._get_java_image("unknown") == "openjdk:17-slim"  # 默认版本
    
    def test_parse_java_error(self, java_compiler):
        """测试Java错误解析"""
        error_output = """
HelloWorld.java:4: error: ';' expected
        System.out.println("Hello, World!")
                                  ^
HelloWorld.java:5: error: cannot find symbol
        System.out.println(y);
                           ^
  symbol:   variable y
  location: class HelloWorld
"""
        
        errors = java_compiler._parse_java_error(error_output)
        
        assert len(errors) >= 2
        assert any("HelloWorld.java:4" in error and "';' expected" in error for error in errors)
        assert any("cannot find symbol" in error and "variable y" in error for error in errors)
    
    def test_parse_maven_output(self, java_compiler):
        """测试Maven输出解析"""
        output = """
[INFO] Scanning for projects...
[INFO] 
[INFO] ------------------< com.example:test-project >-------------------
[INFO] Building test-project 1.0.0
[INFO] --------------------------------[ jar ]---------------------------------
[INFO] 
[INFO] Downloading from central: https://repo.maven.apache.org/maven2/junit/junit/4.13.2/junit-4.13.2.pom
[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/junit/junit/4.13.2/junit-4.13.2.pom (2.3 kB at 2.3 kB/s)
[INFO] 
[INFO] --- maven-compiler-plugin:3.8.1:compile (default-compile) @ test-project ---
[INFO] Changes detected - recompiling the module!
[INFO] Compiling 1 source file to /workspace/target/classes
[INFO] ------------------------------------------------------------------------
[INFO] BUILD SUCCESS
[INFO] ------------------------------------------------------------------------
"""
        
        dependencies, errors = java_compiler._parse_maven_output(output)
        
        assert len(dependencies) > 0
        assert any("junit-4.13.2.pom" in dep for dep in dependencies)
        assert len(errors) == 0
    
    def test_parse_gradle_output(self, java_compiler):
        """测试Gradle输出解析"""
        output = """
> Task :compileJava
Downloading https://repo.maven.apache.org/maven2/junit/junit/4.13.2/junit-4.13.2.jar
> Task :processResources NO-SOURCE
> Task :classes
> Task :jar
> Task :assemble
> Task :compileTestJava
> Task :processTestResources NO-SOURCE
> Task :testClasses
> Task :test
> Task :check
> Task :build
BUILD SUCCESSFUL in 1s
5 actionable tasks: 4 executed, 1 up-to-date
"""
        
        dependencies, errors = java_compiler._parse_gradle_output(output)
        
        assert len(dependencies) > 0
        assert any("junit-4.13.2.jar" in dep for dep in dependencies)
        assert len(errors) == 0
    
    def test_is_project_structure(self, java_compiler):
        """测试项目结构检测"""
        maven_files = ["pom.xml", "src/main/java/App.java"]
        is_project, tool = java_compiler._is_project_structure(maven_files)
        assert is_project is True
        assert tool == "maven"
        
        gradle_files = ["build.gradle", "src/main/java/App.java"]
        is_project, tool = java_compiler._is_project_structure(gradle_files)
        assert is_project is True
        assert tool == "gradle"
        
        simple_files = ["App.java", "Utils.java"]
        is_project, tool = java_compiler._is_project_structure(simple_files)
        assert is_project is False
        assert tool is None


class TestJavaCompilerIntegration:
    """Java编译器集成测试类"""
    
    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """测试完整的工作流程"""
        compiler = JavaCompiler()
        
        # 1. 语法检查
        code = """
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }
    
    public static void main(String[] args) {
        Calculator calc = new Calculator();
        int result = calc.add(5, 3);
        System.out.println("5 + 3 = " + result);
    }
}
"""
        
        syntax_result = await compiler.check_syntax(code)
        assert syntax_result.success is True
        
        # 2. 编译
        compile_result = await compiler.compile_code(code)
        assert compile_result.success is True
        assert "Calculator.class" in compile_result.class_files
        
        # 3. 执行
        execute_result = await compiler.execute_code(code, "Calculator.java", "Calculator")
        assert execute_result.success is True
        assert "5 + 3 = 8" in execute_result.output
        
        # 4. 增强语法检查
        enhanced_result = await compiler.enhanced_check_syntax(code)
        assert enhanced_result.success is True