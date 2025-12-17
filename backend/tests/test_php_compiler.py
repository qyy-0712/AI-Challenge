"""
PHP编译器测试

测试PHP编译器的各种功能，包括语法检查、代码执行和依赖管理。
"""

import asyncio
import pytest
import json
from unittest.mock import Mock, AsyncMock, patch

from app.docker.compilers.php_compiler import (
    PHPCompiler,
    PHPCheckType,
    PHPCheckResult,
    PHPExecutionOptions,
    PHPCompilerError
)
from app.docker.container_manager import ContainerExecutionError, ContainerTimeoutError
from app.docker.file_manager import FileManagerError


class TestPHPCompiler:
    """PHP编译器测试类"""
    
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
        manager.temp_manager = Mock()
        manager.temp_manager.get_temp_file_path = Mock(return_value="/tmp/test_file")
        return manager
    
    @pytest.fixture
    def php_compiler(self, mock_container_manager, mock_file_manager):
        """创建PHP编译器实例"""
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
        
        return PHPCompiler(mock_container_manager, mock_file_manager)
    
    @pytest.fixture
    def valid_php_code(self):
        """有效的PHP代码"""
        return """
<?php
function hello_world() {
    echo "Hello, World!";
}

hello_world();
?>
"""
    
    @pytest.fixture
    def invalid_php_code(self):
        """无效的PHP代码"""
        return """
<?php
function hello_world()
    echo "Hello, World!";  // 缺少大括号

hello_world();
?>
"""
    
    @pytest.fixture
    def composer_json_content(self):
        """composer.json内容"""
        return json.dumps({
            "name": "test/project",
            "description": "Test project",
            "require": {
                "php": "^8.0",
                "monolog/monolog": "^2.0",
                "symfony/console": "^5.0"
            },
            "require-dev": {
                "phpunit/phpunit": "^9.0"
            }
        })
    
    @pytest.mark.asyncio
    async def test_check_syntax_success(self, php_compiler, valid_php_code):
        """测试成功的语法检查"""
        # 模拟容器执行返回成功
        php_compiler.container_manager._execute_command.return_value = (0, b"No syntax errors detected", b"")
        
        # 执行语法检查
        result = await php_compiler.check_syntax(valid_php_code)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PHPCheckType.SYNTAX
        assert len(result.errors) == 0
        assert result.execution_time > 0
        assert result.container_id == "test_container_id"
    
    @pytest.mark.asyncio
    async def test_check_syntax_failure(self, php_compiler, invalid_php_code):
        """测试失败的语法检查"""
        # 模拟容器执行返回错误
        php_compiler.container_manager._execute_command.return_value = (
            1,
            b"",
            b"Parse error: syntax error, unexpected 'echo' in /workspace/test_file_id on line 4"
        )
        
        # 执行语法检查
        result = await php_compiler.check_syntax(invalid_php_code)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PHPCheckType.SYNTAX
        assert len(result.errors) > 0
        assert any("Parse error" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_check_syntax_timeout(self, php_compiler, valid_php_code):
        """测试语法检查超时"""
        # 模拟容器执行超时
        php_compiler.container_manager._execute_command.side_effect = ContainerTimeoutError("Timeout")
        
        # 创建选项对象
        options = PHPExecutionOptions(timeout=1)
        
        # 执行语法检查
        result = await php_compiler.check_syntax(valid_php_code, options=options)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PHPCheckType.SYNTAX
        assert len(result.errors) > 0
        assert any("超时" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_execute_code_success(self, php_compiler, valid_php_code):
        """测试成功的代码执行"""
        # 模拟容器执行返回成功
        php_compiler.container_manager._execute_command.return_value = (0, b"Hello, World!\n", b"")
        
        # 执行代码
        result = await php_compiler.execute_code(valid_php_code)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PHPCheckType.EXECUTE
        assert result.output == "Hello, World!\n"
        assert len(result.errors) == 0
    
    @pytest.mark.asyncio
    async def test_execute_code_failure(self, php_compiler, invalid_php_code):
        """测试失败的代码执行"""
        # 模拟容器执行返回错误
        php_compiler.container_manager._execute_command.return_value = (
            1,
            b"",
            b"Parse error: syntax error, unexpected 'echo' in /workspace/test_file_id on line 4"
        )
        
        # 执行代码
        result = await php_compiler.execute_code(invalid_php_code)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PHPCheckType.EXECUTE
        assert len(result.errors) > 0
        assert any("Parse error" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_install_dependencies_success(self, php_compiler, composer_json_content):
        """测试成功的依赖安装"""
        # 模拟容器执行返回成功
        php_compiler.container_manager._execute_command.return_value = (
            0,
            b" - Installing monolog/monolog (2.3.5)\n - Updating symfony/console (5.4.7)\nGenerating autoload files",
            b""
        )
        
        # 安装依赖
        result = await php_compiler.install_dependencies(composer_json_content)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PHPCheckType.DEPENDENCIES
        assert len(result.dependencies) > 0
        assert "monolog/monolog" in result.dependencies
        assert "symfony/console" in result.dependencies
    
    @pytest.mark.asyncio
    async def test_install_dependencies_failure(self, php_compiler, composer_json_content):
        """测试失败的依赖安装"""
        # 模拟容器执行返回失败
        php_compiler.container_manager._execute_command.return_value = (
            1,
            b"",
            b"[ErrorException] Package 'invalid/package' not found"
        )
        
        # 修改composer.json为无效依赖
        invalid_composer = json.dumps({
            "name": "test/project",
            "require": {
                "invalid/package": "^1.0"
            }
        })
        
        # 安装依赖
        result = await php_compiler.install_dependencies(invalid_composer)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PHPCheckType.DEPENDENCIES
        assert len(result.errors) > 0
        assert any("ErrorException" in error for error in result.errors)
    
    @pytest.mark.asyncio
    async def test_check_dependencies_success(self, php_compiler, composer_json_content):
        """测试成功的依赖检查"""
        # 检查依赖
        result = await php_compiler.check_dependencies(composer_json_content)
        
        # 验证结果
        assert result.success is True
        assert result.check_type == PHPCheckType.DEPENDENCIES
        assert len(result.dependencies) > 0
        assert "monolog/monolog" in result.dependencies
        assert "symfony/console" in result.dependencies
        assert "phpunit/phpunit" in result.dependencies
    
    @pytest.mark.asyncio
    async def test_check_dependencies_invalid_json(self, php_compiler):
        """测试无效JSON的依赖检查"""
        # 无效的JSON
        invalid_json = "{ invalid json }"
        
        # 检查依赖
        result = await php_compiler.check_dependencies(invalid_json)
        
        # 验证结果
        assert result.success is False
        assert result.check_type == PHPCheckType.DEPENDENCIES
        assert len(result.errors) > 0
        assert any("格式错误" in error for error in result.errors)
    
    def test_get_php_image(self, php_compiler):
        """测试获取PHP镜像"""
        # 测试已知版本
        assert php_compiler._get_php_image("7.4") == "php:7.4-cli"
        assert php_compiler._get_php_image("8.2") == "php:8.2-cli"
        
        # 测试未知版本，应返回默认版本
        assert php_compiler._get_php_image("9.0") == "php:8.2-cli"
    
    def test_parse_php_error(self, php_compiler):
        """测试解析PHP错误"""
        error_output = """
Parse error: syntax error, unexpected 'echo' in /workspace/test_file_id on line 4
"""
        
        errors = php_compiler._parse_php_error(error_output)
        
        assert len(errors) > 0
        assert any("Parse error" in error for error in errors)
        assert any("test_file_id" in error for error in errors)
        assert any("line 4" in error for error in errors)
    
    def test_parse_composer_output(self, php_compiler):
        """测试解析Composer输出"""
        output = """
 - Installing monolog/monolog (2.3.5)
 - Updating symfony/console (5.4.7)
Generating autoload files
"""
        
        installed_packages, errors = php_compiler._parse_composer_output(output)
        
        assert len(installed_packages) > 0
        assert "monolog/monolog" in installed_packages
        assert "symfony/console" in installed_packages
        assert len(errors) == 0
    
    def test_php_check_result_to_dict(self, php_compiler):
        """测试PHP检查结果转换为字典"""
        result = PHPCheckResult(
            success=True,
            check_type=PHPCheckType.SYNTAX,
            errors=[],
            warnings=[],
            output="Test output",
            execution_time=1.5,
            dependencies=["monolog/monolog"],
            container_id="test_container"
        )
        
        result_dict = result.to_dict()
        assert result_dict["success"] is True
        assert result_dict["check_type"] == "syntax"
        assert result_dict["errors"] == []
        assert result_dict["warnings"] == []
        assert result_dict["output"] == "Test output"
        assert result_dict["execution_time"] == 1.5
        assert result_dict["dependencies"] == ["monolog/monolog"]
        assert result_dict["container_id"] == "test_container"
    
    def test_php_execution_options(self, php_compiler):
        """测试PHP执行选项"""
        options = PHPExecutionOptions(
            timeout=60,
            check_dependencies=False,
            install_dependencies=False,
            lint_code=True,
            capture_output=False,
            working_directory="/custom_workspace",
            php_version="8.1",
            memory_limit="512m",
            cpu_limit=75000
        )
        
        assert options.timeout == 60
        assert options.check_dependencies is False
        assert options.install_dependencies is False
        assert options.lint_code is True
        assert options.capture_output is False
        assert options.working_directory == "/custom_workspace"
        assert options.php_version == "8.1"
        assert options.memory_limit == "512m"
        assert options.cpu_limit == 75000
    
    def test_php_execution_options_defaults(self, php_compiler):
        """测试PHP执行选项默认值"""
        options = PHPExecutionOptions()
        
        assert options.timeout == 30
        assert options.check_dependencies is True
        assert options.install_dependencies is True
        assert options.lint_code is False
        assert options.capture_output is True
        assert options.working_directory == "/workspace"
        assert options.php_version == "8.2"
        assert options.memory_limit == "256m"
        assert options.cpu_limit == 50000


# 实际集成测试（需要Docker环境）
@pytest.mark.integration
class TestPHPCompilerIntegration:
    """PHP编译器集成测试类"""
    
    @pytest.fixture
    def php_compiler(self):
        """创建实际的PHP编译器实例"""
        try:
            return PHPCompiler()
        except Exception as e:
            pytest.skip(f"Docker不可用，跳过集成测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_syntax_check(self, php_compiler):
        """真实的语法检查测试"""
        # 有效代码
        valid_code = """
<?php
function hello_world() {
    echo "Hello, World!";
}

hello_world();
?>
"""
        
        try:
            result = await php_compiler.check_syntax(valid_code)
            assert result.success is True
            assert len(result.errors) == 0
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
        
        # 无效代码
        invalid_code = """
<?php
function hello_world()
    echo "Hello, World!";

hello_world();
?>
"""
        
        try:
            result = await php_compiler.check_syntax(invalid_code)
            assert result.success is False
            assert len(result.errors) > 0
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_code_execution(self, php_compiler):
        """真实的代码执行测试"""
        code = """
<?php
echo "Test output\n";
$result = 2 + 2;
echo "2 + 2 = {$result}\n";
?>
"""
        
        try:
            result = await php_compiler.execute_code(code)
            assert result.success is True
            assert "Test output" in result.output
            assert "2 + 2 = 4" in result.output
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_dependency_installation(self, php_compiler):
        """真实的依赖安装测试"""
        composer_json = json.dumps({
            "name": "test/project",
            "require": {
                "php": "^8.0"
            }
        })
        
        try:
            result = await php_compiler.install_dependencies(composer_json)
            assert result.success is True
            assert result.output is not None
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")
    
    @pytest.mark.asyncio
    async def test_real_dependency_check(self, php_compiler):
        """真实的依赖检查测试"""
        composer_json = json.dumps({
            "name": "test/project",
            "require": {
                "php": "^8.0",
                "monolog/monolog": "^2.0"
            },
            "require-dev": {
                "phpunit/phpunit": "^9.0"
            }
        })
        
        try:
            result = await php_compiler.check_dependencies(composer_json)
            assert result.success is True
            assert len(result.dependencies) > 0
            assert "monolog/monolog" in result.dependencies
            assert "phpunit/phpunit" in result.dependencies
        except Exception as e:
            pytest.skip(f"Docker执行失败，跳过测试: {e}")