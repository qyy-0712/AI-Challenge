"""
Java编译器简单测试

测试Java编译器的基本功能。
"""

import asyncio
import pytest

from app.docker.compilers.java_compiler import (
    JavaCompiler,
    JavaCheckType,
    JavaExecutionOptions
)


class TestJavaCompilerSimple:
    """Java编译器简单测试类"""
    
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
    }
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
    
    @pytest.mark.asyncio
    async def test_compile_code_valid(self, java_compiler, valid_java_code):
        """测试有效Java代码的编译"""
        result = await java_compiler.compile_code(valid_java_code, "HelloWorld.java")
        
        assert result.success is True
        assert result.check_type == JavaCheckType.COMPILE
        assert len(result.errors) == 0
        assert "HelloWorld.class" in result.class_files
    
    @pytest.mark.asyncio
    async def test_execute_code_valid(self, java_compiler, valid_java_code):
        """测试有效Java代码的执行"""
        result = await java_compiler.execute_code(valid_java_code, "HelloWorld.java", "HelloWorld")
        
        assert result.success is True
        assert result.check_type == JavaCheckType.EXECUTE
        assert len(result.errors) == 0
        assert "Hello, World!" in result.output
        assert "Sum of 1 to 10: 55" in result.output
    
    def test_get_java_image(self, java_compiler):
        """测试获取Java镜像"""
        assert java_compiler._get_java_image("8") == "openjdk:8-slim"
        assert java_compiler._get_java_image("11") == "openjdk:11-slim"
        assert java_compiler._get_java_image("17") == "openjdk:17-slim"
        assert java_compiler._get_java_image("21") == "openjdk:21-slim"
        assert java_compiler._get_java_image("unknown") == "openjdk:17-slim"  # 默认版本
    
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