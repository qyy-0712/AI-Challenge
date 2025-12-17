"""
JavaScript/TypeScript编译器简化测试

不依赖Docker环境的基本功能测试
"""

import pytest
import sys
import os
from unittest.mock import Mock

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.docker.compilers.javascript_compiler import (
    JavaScriptCompiler,
    JavaScriptCheckType,
    JavaScriptCheckResult,
    JavaScriptExecutionOptions,
    JavaScriptSyntaxError,
    JavaScriptDependencyError,
    JavaScriptTypeScriptError
)


class TestJavaScriptCompilerSimple:
    """JavaScript编译器简化测试类"""
    
    def test_compiler_initialization(self):
        """测试编译器初始化"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        assert compiler is not None
        assert compiler.container_manager is mock_container_manager
        assert compiler.file_manager is mock_file_manager
        assert hasattr(compiler, '_cleanup_tasks_started')
        
    def test_detect_language_javascript(self):
        """测试JavaScript语言检测"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        js_code = """
        function greet(name) {
            return `Hello, ${name}!`;
        }
        
        console.log(greet("World"));
        """
        
        # 测试通过文件扩展名检测
        language = compiler._detect_language(js_code, "script.js")
        assert language == "javascript"
        
        # 测试通过文件扩展名检测React组件
        language = compiler._detect_language(js_code, "component.jsx")
        assert language == "javascript"
    
    def test_detect_language_typescript_by_extension(self):
        """测试通过文件扩展名检测TypeScript语言"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        ts_code = """
        function greet(name: string): string {
            return `Hello, ${name}!`;
        }
        """
        
        # 测试.ts文件
        language = compiler._detect_language(ts_code, "script.ts")
        assert language == "typescript"
        
        # 测试.tsx文件
        language = compiler._detect_language(ts_code, "component.tsx")
        assert language == "typescript"
    
    def test_detect_language_typescript_by_content(self):
        """测试通过代码内容检测TypeScript语言"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        # 测试接口定义
        code_with_interface = """
        interface Person {
            name: string;
            age: number;
        }
        
        function greet(person: Person): string {
            return `Hello, ${person.name}!`;
        }
        """
        language = compiler._detect_language(code_with_interface, "script.js")  # 文件扩展名是.js，但内容是TypeScript
        assert language == "typescript"
        
        # 测试类型别名
        code_with_type_alias = """
        type UserID = string | number;
        
        function getUser(id: UserID): User {
            return findUserById(id);
        }
        """
        language = compiler._detect_language(code_with_type_alias, "script.js")
        assert language == "typescript"
        
        # 测试枚举定义
        code_with_enum = """
        enum Color {
            Red,
            Green,
            Blue
        }
        
        const favoriteColor: Color = Color.Blue;
        """
        language = compiler._detect_language(code_with_enum, "script.js")
        assert language == "typescript"
        
        # 测试泛型
        code_with_generics = """
        function identity<T>(arg: T): T {
            return arg;
        }
        
        const result = identity<string>("hello");
        """
        language = compiler._detect_language(code_with_generics, "script.js")
        assert language == "typescript"
    
    def test_is_typescript_file(self):
        """测试TypeScript文件检测"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        assert compiler._is_typescript_file("script.ts") is True
        assert compiler._is_typescript_file("component.tsx") is True
        assert compiler._is_typescript_file("module.d.ts") is True
        assert compiler._is_typescript_file("script.js") is False
        assert compiler._is_typescript_file("component.jsx") is False
        assert compiler._is_typescript_file("style.css") is False
        assert compiler._is_typescript_file("data.json") is False
    
    def test_get_node_image(self):
        """测试Node.js镜像获取"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        # 测试支持的版本
        assert compiler._get_node_image("16") == "node:16-alpine"
        assert compiler._get_node_image("18") == "node:18-alpine"
        assert compiler._get_node_image("20") == "node:20-alpine"
        assert compiler._get_node_image("21") == "node:21-alpine"
        
        # 测试不支持的版本
        assert compiler._get_node_image("14") == "node:18-alpine"  # 默认版本
        assert compiler._get_node_image("invalid") == "node:18-alpine"  # 默认版本
    
    def test_parse_javascript_error(self):
        """测试JavaScript错误解析"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        # 测试堆栈跟踪格式
        error_output = """
        ReferenceError: console is not defined
            at greet (/workspace/script.js:3:5)
            at Object.<anonymous> (/workspace/script.js:6:1)
            at Module._compile (internal/modules/cjs/loader.js:999:30)
        """
        
        errors = compiler._parse_javascript_error(error_output)
        assert len(errors) > 0
        assert any("ReferenceError" in error for error in errors)
        assert any("script.js:3:5" in error for error in errors)
        
        # 测试简单错误格式
        simple_error = "SyntaxError: Unexpected token }"
        errors = compiler._parse_javascript_error(simple_error)
        assert len(errors) == 1
        assert "SyntaxError" in errors[0]
    
    def test_parse_npm_output(self):
        """测试npm输出解析"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        # 测试npm 7+输出格式
        npm_output = """
        npm WARN deprecated package@1.0.0: This package is deprecated
        + lodash@4.17.21
        added 1 package in 2.345s
        """
        
        packages, errors = compiler._parse_npm_output(npm_output)
        assert "lodash@4.17.21" in packages
        assert len(packages) > 0
        
        # 测试有错误的情况
        npm_error_output = """
        npm ERR! code ERESOLVE
        npm ERR! ERESOLVE unable to resolve dependency tree
        npm ERR!
        npm ERR! While resolving: project@1.0.0
        npm ERR! Found: react@17.0.2
        npm ERR! peer dep missing: react@^16.8.0
        """
        
        packages, errors = compiler._parse_npm_output(npm_error_output)
        assert len(errors) > 0
        assert any("npm ERR!" in error for error in errors)
    
    def test_parse_eslint_output(self):
        """测试ESLint输出解析"""
        # 使用mock对象避免Docker连接
        mock_container_manager = Mock()
        mock_file_manager = Mock()
        compiler = JavaScriptCompiler(
            container_manager=mock_container_manager,
            file_manager=mock_file_manager
        )
        
        # 测试JSON格式的ESLint输出
        eslint_json_output = """
        [
            {
                "filePath": "/workspace/script.js",
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 2,
                        "message": "'unused' is defined but never used.",
                        "line": 2,
                        "column": 7,
                        "nodeType": "Identifier",
                        "messageId": "unusedVar"
                    }
                ]
            }
        ]
        """
        
        errors, warnings = compiler._parse_eslint_output(eslint_json_output)
        assert len(errors) > 0
        assert "Line 2, Column 7" in errors[0]
        assert "'unused' is defined but never used" in errors[0]
        
        # 测试纯文本格式输出
        eslint_text_output = """
        /workspace/script.js
          2:7  error  'unused' is defined but never used  no-unused-vars
          3:10  warning  'console' is not defined          no-undef
        
        ✖ 2 problems (1 error, 1 warning)
        """
        
        errors, warnings = compiler._parse_eslint_output(eslint_text_output)
        assert len(errors) > 0
        assert len(warnings) > 0
    
    def test_javascript_check_type_enum(self):
        """测试JavaScriptCheckType枚举"""
        assert JavaScriptCheckType.SYNTAX.value == "syntax"
        assert JavaScriptCheckType.LINT.value == "lint"
        assert JavaScriptCheckType.EXECUTE.value == "execute"
        assert JavaScriptCheckType.DEPENDENCIES.value == "dependencies"
        assert JavaScriptCheckType.TYPE_CHECK.value == "type_check"
    
    def test_javascript_check_result(self):
        """测试JavaScriptCheckResult数据类"""
        result = JavaScriptCheckResult(
            success=True,
            check_type=JavaScriptCheckType.SYNTAX,
            output="Check completed successfully",
            execution_time=0.123,
            container_id="container123",
            language="typescript"
        )
        
        dict_result = result.to_dict()
        assert dict_result["success"] is True
        assert dict_result["check_type"] == "syntax"
        assert dict_result["output"] == "Check completed successfully"
        assert dict_result["execution_time"] == 0.123
        assert dict_result["container_id"] == "container123"
        assert dict_result["language"] == "typescript"
        assert dict_result["errors"] == []
        assert dict_result["warnings"] == []
        assert dict_result["dependencies"] == []
    
    def test_javascript_execution_options(self):
        """测试JavaScriptExecutionOptions数据类"""
        options = JavaScriptExecutionOptions(
            timeout=60,
            node_version="20",
            typescript=True,
            memory_limit="512m",
            package_manager="yarn"
        )
        
        assert options.timeout == 60
        assert options.node_version == "20"
        assert options.typescript is True
        assert options.memory_limit == "512m"
        assert options.package_manager == "yarn"
        assert options.check_dependencies is True  # 默认值
        assert options.install_dependencies is True  # 默认值
    
    def test_custom_exceptions(self):
        """测试自定义异常类"""
        # 测试基础异常
        from app.docker.compilers.javascript_compiler import (
            JavaScriptCompilerError,
            JavaScriptDependencyError,
            JavaScriptSyntaxError,
            JavaScriptTypeScriptError
        )
        
        exception = JavaScriptCompilerError("Test error")
        assert str(exception) == "Test error"
        
        # 测试依赖管理异常
        dep_exception = JavaScriptDependencyError("Dependency error")
        assert isinstance(dep_exception, JavaScriptCompilerError)
        assert str(dep_exception) == "Dependency error"
        
        # 测试语法错误异常
        syntax_exception = JavaScriptSyntaxError("Syntax error")
        assert isinstance(syntax_exception, JavaScriptCompilerError)
        assert str(syntax_exception) == "Syntax error"
        
        # 测试TypeScript错误异常
        ts_exception = JavaScriptTypeScriptError("TypeScript error")
        assert isinstance(ts_exception, JavaScriptCompilerError)
        assert str(ts_exception) == "TypeScript error"


if __name__ == "__main__":
    pytest.main(["-v", __file__])