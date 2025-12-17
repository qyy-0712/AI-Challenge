"""
Python编译器测试

测试Python编译器的各种功能，包括语法检查、代码执行和依赖管理。
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from app.docker.compilers.python_compiler import (
    PythonCompiler,
    PythonCheckType,
    PythonCheckResult,
    PythonExecutionOptions,
    PythonCompilerError
)
from app.docker.container_manager import ContainerExecutionError, ContainerTimeoutError
from app.docker.file_manager import FileManagerError


class TestPythonCompiler:
    """Python编译器测试类"""
    
    @pytest.fixture
    def mock_container_manager(self):
        """模拟容器管理器"""
        manager = Mock()
        manager.get_container_for_language.return_value = "test_container_id"
        manager.create_and_start_container.return_value = "test_container_id"
        manager.client.containers.get.return_value = Mock()
        manager._execute_command.return_value = (0, b"output", b"")
        manager._pool = Mock()
        manager._pool.start_cleanup_task = Mock()
        return manager
    
    @pytest.fixture
    def mock_file_manager(self):
        """模拟文件管理器"""
        manager = Mock()
        manager.temporary_file_context = AsyncMock()
        manager.setup_file_mounts.return_value = {"/host/path": {"bind": "/container/path", "mode": "ro"}}
        manager.start_cleanup_task = AsyncMock()
        manager.cleanup_temp_files = AsyncMock(return_value=1)
        manager.stop_cleanup_task = Mock()
        return manager
    
    @pytest.fixture
    def python_compiler(self, mock_container_manager, mock_file_manager):
        """创建Python编译器实例"""
        # 创建一个简单的异步上下文管理器
        class MockAsyncContextManager:
            def __init__(self, return_value):
                self.return_value = return_value
                
            async def __aenter__(self):
                return self.return_value
                
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
        
        # 设置默认的临时文件上下文模拟
        mock_file_manager.temporary_file_context.return_value = MockAsyncContextManager("test_file_id")
        
        return PythonCompiler(mock_container_manager, mock_file_manager)
    
    @pytest.fixture
    def valid_python_code(self):
        """有效的Python代码"""
        return """
def hello_world():
    print("Hello, World!")

if __name__ == "__main__":
    hello_world()
"""
    
    @pytest.fixture
    def invalid_python_code(self):
        """无效的Python代码"""
        return """
def hello_world()
    print("Hello, World!")  # 缺少冒号

if __name__ == "__main__":
    hello_world()
"""
    
    @pytest.fixture
    def requirements_content(self):
        """requirements.txt内容"""
        return """
requests>=2.25.0
numpy==1.21.0
pandas
"""
    
    @pytest.mark.asyncio
    async def test_check_syntax_success(self, python_compiler, valid_python_code):
        """测试成功的语法检查"""
        # 执行语法检查
        result = await python_compiler.check_syntax(valid_python_code)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PythonCheckType.SYNTAX
        assert len(result.errors) == 0
        assert result.execution_time > 0
        assert result.container_id == "test_container_id"
    
    @pytest.mark.asyncio
    async def test_check_syntax_failure(self, python_compiler, invalid_python_code):
        """测试失败的语法检查"""
        # 模拟容器执行返回错误
        python_compiler.container_manager._execute_command.return_value = (1, b"", b"SyntaxError: invalid syntax")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行语法检查
        result = await python_compiler.check_syntax(invalid_python_code)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PythonCheckType.SYNTAX
        assert len(result.errors) > 0
        assert any("SyntaxError" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_check_syntax_timeout(self, python_compiler, valid_python_code):
        """测试语法检查超时"""
        # 模拟容器执行超时
        python_compiler.container_manager._execute_command.side_effect = ContainerTimeoutError("Timeout")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行语法检查
        result = await python_compiler.check_syntax(valid_python_code, timeout=1)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PythonCheckType.SYNTAX
        assert len(result.errors) > 0
        assert any("超时" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_execute_code_success(self, python_compiler, valid_python_code):
        """测试成功的代码执行"""
        # 模拟容器执行返回成功
        python_compiler.container_manager._execute_command.return_value = (0, b"Hello, World!\n", b"")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行代码
        result = await python_compiler.execute_code(valid_python_code)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PythonCheckType.EXECUTE
        assert result.output == "Hello, World!\n"
        assert len(result.errors) == 0
    
    @pytest.mark.asyncio
    async def test_execute_code_failure(self, python_compiler, invalid_python_code):
        """测试失败的代码执行"""
        # 模拟容器执行返回错误
        python_compiler.container_manager._execute_command.return_value = (1, b"", b"SyntaxError: invalid syntax")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行代码
        result = await python_compiler.execute_code(invalid_python_code)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PythonCheckType.EXECUTE
        assert len(result.errors) > 0
        assert any("SyntaxError" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_install_dependencies_success(self, python_compiler):
        """测试成功的依赖安装"""
        # 模拟容器执行返回成功
        python_compiler.container_manager._execute_command.return_value = (
            0, 
            b"Successfully installed requests-2.25.1 numpy-1.21.0", 
            b""
        )
        
        # 安装依赖
        result = await python_compiler.install_dependencies(["requests", "numpy"])
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PythonCheckType.DEPENDENCIES
        assert len(result.dependencies) > 0
        assert "requests-2.25.1" in result.dependencies
        assert "numpy-1.21.0" in result.dependencies
    
    @pytest.mark.asyncio
    async def test_install_dependencies_failure(self, python_compiler):
        """测试失败的依赖安装"""
        # 模拟容器执行返回失败
        python_compiler.container_manager._execute_command.return_value = (
            1, 
            b"", 
            b"ERROR: Package 'invalid-package' not found"
        )
        
        # 安装依赖
        result = await python_compiler.install_dependencies(["invalid-package"])
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PythonCheckType.DEPENDENCIES
        assert len(result.errors) > 0
        assert any("ERROR" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_check_dependencies_success(self, python_compiler, requirements_content):
        """测试成功的依赖检查"""
        # 模拟容器执行返回成功
        python_compiler.container_manager._execute_command.return_value = (0, b"No broken requirements", b"")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 检查依赖
        result = await python_compiler.check_dependencies(requirements_content)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PythonCheckType.DEPENDENCIES
        assert len(result.dependencies) > 0
        assert "requests" in result.dependencies
        assert "numpy" in result.dependencies
        assert "pandas" in result.dependencies
    
    @pytest.mark.asyncio
    async def test_check_dependencies_failure(self, python_compiler, requirements_content):
        """测试失败的依赖检查"""
        # 模拟容器执行返回失败
        python_compiler.container_manager._execute_command.return_value = (
            1, 
            b"", 
            b"requests 2.25.0 has requirement urllib3<1.27,>=1.21.1, but you have urllib3 1.26.0"
        )
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 检查依赖
        result = await python_compiler.check_dependencies(requirements_content)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PythonCheckType.DEPENDENCIES
        assert len(result.errors) > 0
        assert any("urllib3" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_lint_code_success(self, python_compiler, valid_python_code):
        """测试成功的代码风格检查"""
        # 模拟安装和执行成功
        python_compiler.container_manager._execute_command.return_value = (0, b"", b"")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行代码风格检查
        result = await python_compiler.lint_code(valid_python_code)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PythonCheckType.LINT
        assert len(result.warnings) == 0
    
    @pytest.mark.asyncio
    async def test_lint_code_warnings(self, python_compiler):
        """测试有警告的代码风格检查"""
        # 有问题的代码
        problematic_code = """
import os
import sys  # 未使用的导入

def unused_function():
    x = 1  # 未使用的变量
    return x
"""
        
        # 模拟容器执行返回警告
        python_compiler.container_manager._execute_command.return_value = (
            1, 
            b"test.py:2: 'sys' imported but unused\n"
            b"test.py:4: 'x' assigned but never used", 
            b""
        )
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行代码风格检查
        result = await python_compiler.lint_code(problematic_code)
        
        # 验证结果
        assert result.success is False  # pyflakes返回非零退出码表示有问题
        assert result.check_type == PythonCheckType.LINT
        assert len(result.warnings) > 0
        assert any("unused" in warning for warning in result.warnings)
    
    @pytest.mark.asyncio
    async def test_comprehensive_check_success(self, python_compiler, valid_python_code):
        """测试成功的综合检查"""
        # 模拟所有操作成功
        python_compiler.container_manager._execute_command.return_value = (0, b"output", b"")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 配置选项
        options = PythonExecutionOptions(
            check_dependencies=True,
            lint_code=True,
            capture_output=True
        )
        
        # 执行综合检查
        result = await python_compiler.comprehensive_check(
            valid_python_code,
            requirements=["requests"],
            options=options
        )
        
        # 验证结果
        assert result.success is True
        assert len(result.errors) == 0
        assert result.output == "output"
        assert len(result.dependencies) > 0
        assert result.execution_time > 0
    
    @pytest.mark.asyncio
    async def test_comprehensive_check_syntax_error(self, python_compiler, invalid_python_code):
        """测试有语法错误的综合检查"""
        # 模拟语法检查失败
        python_compiler.container_manager._execute_command.return_value = (1, b"", b"SyntaxError: invalid syntax")
        
        # 模拟临时文件上下文
        async def mock_temp_file_context(content, filename):
            yield "test_file_id"
        
        python_compiler.file_manager.temporary_file_context.side_effect = mock_temp_file_context
        
        # 执行综合检查
        result = await python_compiler.comprehensive_check(invalid_python_code)
        
        # 验证结果
        assert result.success is False
        assert len(result.errors) > 0
        assert any("SyntaxError" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_cleanup(self, python_compiler):
        """测试清理资源"""
        # 执行清理
        await python_compiler.cleanup()
        
        # 验证清理方法被调用
        python_compiler.file_manager.cleanup_temp_files.assert_called_once()
        python_compiler.file_manager.stop_cleanup_task.assert_called_once()
        python_compiler.container_manager._pool.stop_cleanup_task.assert_called_once()
    
    def test_get_python_image(self, python_compiler):
        """测试获取Python镜像"""
        # 测试已知版本
        assert python_compiler._get_python_image("3.8") == "python:3.8-slim"
        assert python_compiler._get_python_image("3.11") == "python:3.11-slim"
        
        # 测试未知版本，应返回默认版本
        assert python_compiler._get_python_image("3.99") == "python:3.11-slim"
    
    def test_parse_python_error(self, python_compiler):
        """测试解析Python错误"""
        error_output = """
  File "script.py", line 3
    def hello_world()
      ^
SyntaxError: invalid syntax
"""
        
        errors = python_compiler._parse_python_error(error_output)
        
        assert len(errors) > 0
        assert any("script.py" in error for error in errors)
        assert any("line 3" in error for error in errors)
        assert any("SyntaxError" in error for error in errors)
    
    def test_parse_pip_output(self, python_compiler):
        """测试解析pip输出"""
        output = """
Collecting requests
  Downloading requests-2.25.1-py2.py3-none-any.whl (63 kB)
Collecting numpy
  Downloading numpy-1.21.0-cp38-cp38-manylinux_2_12_x86_64.manylinux2010_x86_64.whl (15.7 MB)
Successfully installed requests-2.25.1 numpy-1.21.0
"""
        
        installed_packages, errors = python_compiler._parse_pip_output(output)
        
        assert len(installed_packages) > 0
        assert "requests-2.25.1" in installed_packages
        assert "numpy-1.21.0" in installed_packages
        assert len(errors) == 0


# 实际集成测试（需要Docker环境）
@pytest.mark.integration
class TestPythonCompilerIntegration:
    """Python编译器集成测试类"""
    
    @pytest.fixture
    def python_compiler(self):
        """创建实际的Python编译器实例"""
        from app.docker.compilers.python_compiler import PythonCompiler
        return PythonCompiler()
    
    @pytest.mark.asyncio
    async def test_real_syntax_check(self, python_compiler):
        """真实的语法检查测试"""
        # 有效代码
        valid_code = """
def hello_world():
    print("Hello, World!")

if __name__ == "__main__":
    hello_world()
"""
        
        result = await python_compiler.check_syntax(valid_code)
        assert result.success is True
        assert len(result.errors) == 0
        
        # 无效代码
        invalid_code = """
def hello_world()
    print("Hello, World!")
"""
        
        result = await python_compiler.check_syntax(invalid_code)
        assert result.success is False
        assert len(result.errors) > 0
    
    @pytest.mark.asyncio
    async def test_real_code_execution(self, python_compiler):
        """真实的代码执行测试"""
        code = """
print("Test output")
result = 2 + 2
print(f"2 + 2 = {result}")
"""
        
        result = await python_compiler.execute_code(code)
        assert result.success is True
        assert "Test output" in result.output
        assert "2 + 2 = 4" in result.output
    
    @pytest.mark.asyncio
    async def test_real_dependency_installation(self, python_compiler):
        """真实的依赖安装测试"""
        # 安装简单的包
        result = await python_compiler.install_dependencies(["requests"])
        assert result.success is True
        assert len(result.dependencies) > 0
        assert any("requests" in dep for dep in result.dependencies)
    
    @pytest.mark.asyncio
    async def test_real_comprehensive_check(self, python_compiler):
        """真实的综合检查测试"""
        code = """
import requests

def fetch_example():
    response = requests.get("https://example.com")
    return response.status_code

if __name__ == "__main__":
    status = fetch_example()
    print(f"Status code: {status}")
"""
        
        options = PythonExecutionOptions(
            check_dependencies=True,
            lint_code=True,
            capture_output=True,
            timeout=30
        )
        
        result = await python_compiler.comprehensive_check(
            code, 
            requirements=["requests"],
            options=options
        )
        
        # 由于网络请求可能失败，主要检查语法和依赖安装
        assert len(result.errors) == 0 or any("requests" in error for error in result.errors)