"""
文件管理器测试用例

测试文件系统安全交互机制的各种功能，包括路径验证、文件传输、权限控制等。
"""

import asyncio
import os
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from app.docker.file_manager import (
    FileManager, 
    TemporaryFileManager, 
    SecurePathValidator,
    DockerFileTransfer,
    FileTransferConfig,
    FileMetadata,
    FilePermission,
    FileOperationType,
    FileManagerError,
    FileSecurityError,
    FileTransferError,
    PathValidationError
)


class TestSecurePathValidator:
    """安全路径验证器测试"""
    
    def test_validate_safe_path(self):
        """测试安全路径验证"""
        validator = SecurePathValidator()
        
        # 安全路径
        safe_path = validator.validate_path("/tmp/test")
        assert str(safe_path) == str(Path("/tmp/test").resolve())
        
        # 相对路径
        safe_path = validator.validate_path("test.txt")
        assert safe_path.is_absolute()
    
    def test_validate_blocked_path(self):
        """测试禁止路径验证"""
        validator = SecurePathValidator()
        
        # 禁止路径
        with pytest.raises(PathValidationError):
            validator.validate_path("/etc/passwd")
        
        with pytest.raises(PathValidationError):
            validator.validate_path("/usr/bin")
    
    def test_validate_path_traversal(self):
        """测试路径遍历攻击防护"""
        validator = SecurePathValidator()
        
        # 路径遍历
        with pytest.raises(PathValidationError):
            validator.validate_path("../../../etc/passwd")
        
        with pytest.raises(PathValidationError):
            validator.validate_path("/tmp/../../../etc/passwd")
    
    def test_is_safe_filename(self):
        """测试安全文件名检查"""
        validator = SecurePathValidator()
        
        # 安全文件名
        assert validator.is_safe_filename("test.py") == True
        assert validator.is_safe_filename("myfile_123.txt") == True
        
        # 危险字符
        assert validator.is_safe_filename("test<1>.py") == False
        assert validator.is_safe_filename("test|pipe.py") == False
        assert validator.is_safe_filename("test:colon.py") == False
        
        # Windows保留名称
        assert validator.is_safe_filename("CON.txt") == False
        assert validator.is_safe_filename("PRN.py") == False


class TestTemporaryFileManager:
    """临时文件管理器测试"""
    
    @pytest.fixture
    def temp_config(self):
        """临时文件配置"""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield FileTransferConfig(
                temp_dir=temp_dir,
                max_temp_files=5,
                cleanup_interval=1
            )
    
    @pytest.fixture
    def temp_manager(self, temp_config):
        """临时文件管理器实例"""
        return TemporaryFileManager(temp_config)
    
    def test_create_temp_file(self, temp_manager):
        """测试创建临时文件"""
        content = b"Hello, World!"
        filename = "test.txt"
        
        file_id = temp_manager.create_temp_file(content, filename)
        
        assert file_id is not None
        
        # 检查文件元数据
        metadata = temp_manager.get_temp_file(file_id)
        assert metadata is not None
        assert metadata.size == len(content)
        assert metadata.checksum is not None
        assert metadata.is_temporary == True
        
        # 检查文件存在
        file_path = temp_manager.get_temp_file_path(file_id)
        assert file_path is not None
        assert file_path.exists()
        assert file_path.read_bytes() == content
    
    def test_delete_temp_file(self, temp_manager):
        """测试删除临时文件"""
        content = b"Hello, World!"
        filename = "test.txt"
        
        file_id = temp_manager.create_temp_file(content, filename)
        file_path = temp_manager.get_temp_file_path(file_id)
        
        assert file_path.exists()
        
        # 删除文件
        result = temp_manager.delete_temp_file(file_id)
        assert result == True
        
        # 检查文件已删除
        assert not file_path.exists()
        assert temp_manager.get_temp_file(file_id) is None
    
    def test_max_temp_files_limit(self, temp_manager):
        """测试临时文件数量限制"""
        content = b"test"
        
        # 创建最大数量的文件
        file_ids = []
        for i in range(temp_manager.config.max_temp_files):
            file_id = temp_manager.create_temp_file(content, f"test{i}.txt")
            file_ids.append(file_id)
        
        # 尝试创建超出限制的文件
        with pytest.raises(FileTransferError):
            temp_manager.create_temp_file(content, "exceed.txt")
    
    @pytest.mark.asyncio
    async def test_cleanup_task(self, temp_manager):
        """测试清理任务"""
        content = b"test"
        
        # 创建临时文件
        file_id = temp_manager.create_temp_file(content, "test.txt")
        assert temp_manager.get_temp_file(file_id) is not None
        
        # 启动清理任务
        await temp_manager.start_cleanup_task()
        
        # 等待清理
        await asyncio.sleep(temp_manager.config.cleanup_interval + 0.1)
        
        # 检查文件已清理
        assert temp_manager.get_temp_file(file_id) is None
        
        # 停止清理任务
        temp_manager.stop_cleanup_task()


class TestDockerFileTransfer:
    """Docker文件传输测试"""
    
    @pytest.fixture
    def mock_container(self):
        """模拟Docker容器"""
        container = Mock()
        container.id = "test_container_id"
        return container
    
    @pytest.fixture
    def docker_transfer(self):
        """Docker文件传输实例"""
        with patch('app.docker.file_manager.docker.from_env'):
            return DockerFileTransfer(Mock())
    
    def test_create_volume_mount(self, docker_transfer):
        """测试创建卷挂载配置"""
        with tempfile.TemporaryDirectory() as temp_dir:
            host_path = Path(temp_dir) / "test"
            container_path = "/container/test"
            
            mount = docker_transfer.create_volume_mount(host_path, container_path)
            
            assert str(host_path.resolve()) in mount
            assert mount[str(host_path.resolve())]["bind"] == container_path
            assert mount[str(host_path.resolve())]["mode"] == "ro"
    
    def test_validate_blocked_mount_path(self, docker_transfer):
        """测试验证禁止的挂载路径"""
        with pytest.raises(FileTransferError):
            docker_transfer.create_volume_mount("/etc/passwd", "/container/test")
    
    @patch('app.docker.file_manager.Path.exists')
    @patch('app.docker.file_manager.Path.open')
    def test_copy_to_container(self, mock_open, mock_exists, docker_transfer, mock_container):
        """测试复制文件到容器"""
        # 模拟文件存在
        mock_exists.return_value = True
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file
        
        # 模拟容器操作
        mock_container.put_archive = Mock()
        
        with tempfile.NamedTemporaryFile() as temp_file:
            result = docker_transfer.copy_to_container(
                mock_container, 
                temp_file.name, 
                "/container/test"
            )
        
        assert result == True
        mock_container.put_archive.assert_called_once()


class TestFileManager:
    """文件管理器测试"""
    
    @pytest.fixture
    def file_config(self):
        """文件管理器配置"""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield FileTransferConfig(
                temp_dir=temp_dir,
                max_file_size=1024 * 1024,  # 1MB
                max_temp_files=10
            )
    
    @pytest.fixture
    def file_manager(self, file_config):
        """文件管理器实例"""
        with patch('app.docker.file_manager.docker.from_env'):
            return FileManager(file_config)
    
    def test_validate_file_content(self, file_manager):
        """测试文件内容验证"""
        # 有效文件
        valid_content = b"print('Hello, World!')"
        valid_filename = "test.py"
        
        assert file_manager.validate_file_content(valid_content, valid_filename) == True
        
        # 文件过大
        large_content = b"x" * (file_manager.config.max_file_size + 1)
        with pytest.raises(FileSecurityError):
            file_manager.validate_file_content(large_content, valid_filename)
        
        # 不允许的扩展名
        with pytest.raises(FileSecurityError):
            file_manager.validate_file_content(valid_content, "test.exe")
        
        # 不安全的文件名
        with pytest.raises(FileSecurityError):
            file_manager.validate_file_content(valid_content, "test<1>.py")
    
    def test_create_secure_temp_file(self, file_manager):
        """测试创建安全临时文件"""
        content = b"print('Hello, World!')"
        filename = "test.py"
        
        file_id = file_manager.create_secure_temp_file(content, filename)
        
        assert file_id is not None
        
        # 检查文件信息
        file_info = file_manager.get_temp_file_info(file_id)
        assert file_info is not None
        assert file_info["size"] == len(content)
        assert file_info["is_temporary"] == True
    
    def test_setup_file_mounts(self, file_manager):
        """测试设置文件挂载"""
        content = b"test"
        filename = "test.py"
        
        # 创建临时文件
        file_id = file_manager.create_secure_temp_file(content, filename)
        
        # 设置挂载
        mounts = file_manager.setup_file_mounts([file_id], "/workspace")
        
        assert len(mounts) == 1
        mount_path = list(mounts.keys())[0]
        assert mount_path.endswith(file_id)
        assert mounts[mount_path]["mode"] == "ro"
    
    @pytest.mark.asyncio
    async def test_cleanup_temp_files(self, file_manager):
        """测试清理临时文件"""
        content = b"test"
        filename = "test.py"
        
        # 创建临时文件
        file_id = file_manager.create_secure_temp_file(content, filename)
        assert file_manager.get_temp_file_info(file_id) is not None
        
        # 清理指定文件
        count = await file_manager.cleanup_temp_files([file_id])
        assert count == 1
        assert file_manager.get_temp_file_info(file_id) is None
        
        # 创建更多文件
        file_ids = []
        for i in range(3):
            file_id = file_manager.create_secure_temp_file(content, f"test{i}.py")
            file_ids.append(file_id)
        
        # 清理所有文件
        count = await file_manager.cleanup_temp_files()
        assert count == 3
    
    @pytest.mark.asyncio
    async def test_temporary_file_context(self, file_manager):
        """测试临时文件上下文管理器"""
        content = b"Hello, World!"
        filename = "test.txt"
        
        async with file_manager.temporary_file_context(content, filename) as file_id:
            assert file_id is not None
            assert file_manager.get_temp_file_info(file_id) is not None
            
            # 在上下文中可以访问文件
            file_path = file_manager.temp_manager.get_temp_file_path(file_id)
            assert file_path is not None
            assert file_path.read_bytes() == content
        
        # 退出上下文后文件应被清理
        assert file_manager.get_temp_file_info(file_id) is None
    
    def test_get_file_checksum(self, file_manager):
        """测试计算文件校验和"""
        with tempfile.NamedTemporaryFile() as temp_file:
            content = b"Hello, World!"
            temp_file.write(content)
            temp_file.flush()
            
            checksum1 = file_manager.get_file_checksum(temp_file.name)
            checksum2 = file_manager.get_file_checksum(temp_file.name)
            
            assert checksum1 == checksum2
            assert len(checksum1) == 64  # SHA256 hex length
    
    def test_verify_file_integrity(self, file_manager):
        """测试验证文件完整性"""
        content = b"Hello, World!"
        filename = "test.txt"
        
        # 创建临时文件
        file_id = file_manager.create_secure_temp_file(content, filename)
        
        # 获取正确的校验和
        file_path = file_manager.temp_manager.get_temp_file_path(file_id)
        correct_checksum = file_manager.get_file_checksum(file_path)
        
        # 验证完整性
        assert file_manager.verify_file_integrity(file_id, correct_checksum) == True
        assert file_manager.verify_file_integrity(file_id, "wrong_checksum") == False
        
        # 不存在的文件
        assert file_manager.verify_file_integrity("nonexistent", "checksum") == False


class TestFileTransferIntegration:
    """文件传输集成测试"""
    
    @pytest.fixture
    def integrated_file_manager(self):
        """集成的文件管理器"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = FileTransferConfig(
                temp_dir=temp_dir,
                max_file_size=10 * 1024 * 1024,
                max_temp_files=20
            )
            with patch('app.docker.file_manager.docker.from_env'):
                manager = FileManager(config)
                yield manager
    
    def test_python_file_workflow(self, integrated_file_manager):
        """测试Python文件工作流"""
        python_code = b"""
def hello_world():
    print('Hello, World!')
    return True

if __name__ == '__main__':
    hello_world()
"""
        filename = "hello.py"
        
        # 创建临时文件
        file_id = integrated_file_manager.create_secure_temp_file(python_code, filename)
        
        # 验证文件信息
        file_info = integrated_file_manager.get_temp_file_info(file_id)
        assert file_info is not None
        assert file_info["size"] == len(python_code)
        
        # 设置容器挂载
        mounts = integrated_file_manager.setup_file_mounts([file_id], "/app")
        assert len(mounts) == 1
        
        # 计算并验证校验和
        file_path = integrated_file_manager.temp_manager.get_temp_file_path(file_id)
        checksum = integrated_file_manager.get_file_checksum(file_path)
        assert integrated_file_manager.verify_file_integrity(file_id, checksum) == True
    
    def test_multiple_files_workflow(self, integrated_file_manager):
        """测试多文件工作流"""
        files = [
            (b"def main(): pass", "main.py"),
            (b"import unittest", "test_main.py"),
            (b"README content", "README.md")
        ]
        
        file_ids = []
        
        # 创建多个临时文件
        for content, filename in files:
            file_id = integrated_file_manager.create_secure_temp_file(content, filename)
            file_ids.append(file_id)
        
        # 设置挂载
        mounts = integrated_file_manager.setup_file_mounts(file_ids, "/workspace")
        assert len(mounts) == len(files)
        
        # 验证所有文件
        for file_id in file_ids:
            file_info = integrated_file_manager.get_temp_file_info(file_id)
            assert file_info is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])