"""
JavaScript/TypeScript编译器测试
"""

import asyncio
import pytest
import os
import sys

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.docker.compilers.javascript_compiler import (
    JavaScriptCompiler,
    JavaScriptCheckType,
    JavaScriptCheckResult,
    JavaScriptExecutionOptions
)


class TestJavaScriptCompiler:
    """JavaScript编译器测试类"""
    
    @pytest.fixture
    def compiler(self):
        """创建编译器实例"""
        return JavaScriptCompiler()
    
    @pytest.mark.asyncio
    async def test_check_javascript_syntax_valid(self, compiler):
        """测试有效的JavaScript代码语法检查"""
        code = """
        function greet(name) {
            return `Hello, ${name}!`;
        }
        
        console.log(greet("World"));
        """
        
        result = await compiler.check_syntax(code, "test.js")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.success is True
        assert result.check_type == JavaScriptCheckType.SYNTAX
        assert result.language == "javascript"
        assert len(result.errors) == 0
    
    @pytest.mark.asyncio
    async def test_check_javascript_syntax_invalid(self, compiler):
        """测试无效的JavaScript代码语法检查"""
        code = """
        function greet(name) {
            return `Hello, ${name}!`
        }  // 缺少分号
        
        console.log(greet("World") // 缺少右括号
        """
        
        result = await compiler.check_syntax(code, "test.js")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.success is False
        assert result.check_type == JavaScriptCheckType.SYNTAX
        assert result.language == "javascript"
        assert len(result.errors) > 0
    
    @pytest.mark.asyncio
    async def test_check_typescript_syntax_valid(self, compiler):
        """测试有效的TypeScript代码语法检查"""
        code = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}! You are ${person.age} years old.`;
        }
        
        const user: Person = { name: "Alice", age: 30 };
        console.log(greet(user));
        """
        
        result = await compiler.check_syntax(code, "test.ts")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.success is True
        assert result.check_type == JavaScriptCheckType.SYNTAX
        assert result.language == "typescript"
        assert len(result.errors) == 0
    
    @pytest.mark.asyncio
    async def test_check_typescript_syntax_invalid(self, compiler):
        """测试无效的TypeScript代码语法检查"""
        code = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}! You are ${person.age} years old.`;
        }
        
        const user: Person = { name: "Bob" };  // 缺少age属性
        console.log(greet(user));
        """
        
        result = await compiler.check_syntax(code, "test.ts")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.check_type == JavaScriptCheckType.SYNTAX
        assert result.language == "typescript"
        # TypeScript可能会报告类型错误，但不一定是语法错误
    
    @pytest.mark.asyncio
    async def test_execute_javascript_code(self, compiler):
        """测试JavaScript代码执行"""
        code = """
        function fibonacci(n) {
            if (n <= 1) return n;
            return fibonacci(n - 1) + fibonacci(n - 2);
        }
        
        console.log(fibonacci(10));
        """
        
        result = await compiler.execute_code(code, "fibonacci.js")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.success is True
        assert result.check_type == JavaScriptCheckType.EXECUTE
        assert result.language == "javascript"
        assert "55" in result.output  # fibonacci(10) = 55
    
    @pytest.mark.asyncio
    async def test_execute_typescript_code(self, compiler):
        """测试TypeScript代码执行"""
        code = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}! You are ${person.age} years old.`;
        }
        
        const user: Person = { name: "Alice", age: 30 };
        console.log(greet(user));
        """
        
        options = JavaScriptExecutionOptions(typescript=True)
        result = await compiler.execute_code(code, "greet.ts", options)
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.success is True
        assert result.check_type == JavaScriptCheckType.EXECUTE
        assert result.language == "typescript"
        assert "Hello, Alice!" in result.output
    
    @pytest.mark.asyncio
    async def test_install_dependencies_npm(self, compiler):
        """测试使用npm安装依赖"""
        dependencies = ["lodash", "axios"]
        
        result = await compiler.install_dependencies(dependencies)
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.check_type == JavaScriptCheckType.DEPENDENCIES
        assert result.language == "javascript"
        # 检查是否安装了依赖包
        assert len(result.dependencies) > 0
    
    @pytest.mark.asyncio
    async def test_check_dependencies_from_package_json(self, compiler):
        """测试从package.json检查依赖"""
        package_json = """
        {
            "name": "test-project",
            "version": "1.0.0",
            "description": "Test project",
            "dependencies": {
                "express": "^4.17.1",
                "lodash": "^4.17.21"
            },
            "devDependencies": {
                "jest": "^27.0.6"
            }
        }
        """
        
        result = await compiler.check_dependencies(package_json)
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.check_type == JavaScriptCheckType.DEPENDENCIES
        assert result.language == "javascript"
        # 检查是否提取了依赖列表
        assert len(result.dependencies) >= 3  # express, lodash, jest
        assert "express" in result.dependencies
        assert "lodash" in result.dependencies
        assert "jest" in result.dependencies
    
    @pytest.mark.asyncio
    async def test_lint_javascript_code(self, compiler):
        """测试JavaScript代码风格检查"""
        code = """
        function greet(name) {
            var message = "Hello, " + name + "!";
            console.log(message);
            return message;
        }
        
        greet("World");
        """
        
        result = await compiler.lint_code(code, "greet.js")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.check_type == JavaScriptCheckType.LINT
        assert result.language == "javascript"
        # ESLint可能会报告一些风格建议或警告
    
    @pytest.mark.asyncio
    async def test_type_check_typescript_code_valid(self, compiler):
        """测试有效的TypeScript代码类型检查"""
        code = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}! You are ${person.age} years old.`;
        }
        
        const user: Person = { name: "Alice", age: 30 };
        console.log(greet(user));
        """
        
        result = await compiler.type_check(code, "greet.ts")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.success is True
        assert result.check_type == JavaScriptCheckType.TYPE_CHECK
        assert result.language == "typescript"
        assert len(result.errors) == 0
    
    @pytest.mark.asyncio
    async def test_type_check_typescript_code_invalid(self, compiler):
        """测试无效的TypeScript代码类型检查"""
        code = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}! You are ${person.age} years old.`;
        }
        
        const user = { name: "Bob", age: "thirty" };  // age应该是数字，不是字符串
        console.log(greet(user));
        """
        
        result = await compiler.type_check(code, "greet.ts")
        
        assert isinstance(result, JavaScriptCheckResult)
        assert result.check_type == JavaScriptCheckType.TYPE_CHECK
        assert result.language == "typescript"
        # 应该报告类型错误
        assert len(result.errors) > 0
    
    def test_detect_language_javascript(self, compiler):
        """测试JavaScript语言检测"""
        js_code = """
        function greet(name) {
            return `Hello, ${name}!`;
        }
        
        console.log(greet("World"));
        """
        
        language = compiler._detect_language(js_code, "script.js")
        assert language == "javascript"
    
    def test_detect_language_typescript_by_extension(self, compiler):
        """测试通过文件扩展名检测TypeScript语言"""
        ts_code = """
        function greet(name: string): string {
            return `Hello, ${name}!`;
        }
        """
        
        language = compiler._detect_language(ts_code, "script.ts")
        assert language == "typescript"
    
    def test_detect_language_typescript_by_content(self, compiler):
        """测试通过代码内容检测TypeScript语言"""
        ts_code = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}!`;
        }
        """
        
        language = compiler._detect_language(ts_code, "script.js")  # 文件扩展名是.js，但内容是TypeScript
        assert language == "typescript"
    
    def test_is_typescript_file(self, compiler):
        """测试TypeScript文件检测"""
        assert compiler._is_typescript_file("script.ts") is True
        assert compiler._is_typescript_file("component.tsx") is True
        assert compiler._is_typescript_file("script.js") is False
        assert compiler._is_typescript_file("script.jsx") is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])