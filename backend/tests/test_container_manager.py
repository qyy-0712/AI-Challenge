"""
Docker容器管理器单元测试
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch, MagicMock
from docker.models.containers import Container

from app.docker.container_manager import (
    ContainerManager, 
    ContainerConfig, 
    ContainerPool, 
    ContainerWrapper,
    ResourceLimits,
    SecurityConfig,
    ContainerStatus,
    ContainerManagerError,
    ContainerCreationError,
    ContainerExecutionError
)


class TestResourceLimits:
    """测试资源限制配置"""
    
    def test_default_resource_limits(self):
        """测试默认资源限制"""
        limits = ResourceLimits()
        assert limits.memory == "256m"
        assert limits.cpu_quota == 50000
        assert limits.cpu_period == 100000
        assert limits.blkio_weight == 0
    
    def test_custom_resource_limits(self):
        """测试自定义资源限制"""
        limits = ResourceLimits(
            memory="512m",
            cpu_quota=100000,
            cpu_period=100000,
            blkio_weight=500
        )
        assert limits.memory == "512m"
        assert limits.cpu_quota == 100000
        assert limits.cpu_period == 100000
        assert limits.blkio_weight == 500
    
    def test_to_dict(self):
        """测试转换为字典"""
        limits = ResourceLimits(
            memory="512m",
            cpu_quota=100000,
            blkio_weight=500
        )
        config = limits.to_dict()
        
        expected = {
            "mem_limit": "512m",
            "cpu_quota": 100000,
            "cpu_period": 100000,
            "blkio_weight": 500
        }
        assert config == expected


class TestSecurityConfig:
    """测试安全配置"""
    
    def test_default_security_config(self):
        """测试默认安全配置"""
        config = SecurityConfig()
        assert config.read_only is True
        assert config.no_network is True
        assert config.drop_all_capabilities is True
        assert config.user is None
        assert config.tmpfs_size == "100m"
    
    def test_custom_security_config(self):
        """测试自定义安全配置"""
        config = SecurityConfig(
            read_only=False,
            no_network=False,
            drop_all_capabilities=False,
            user="nobody",
            tmpfs_size="200m"
        )
        assert config.read_only is False
        assert config.no_network is False
        assert config.drop_all_capabilities is False
        assert config.user == "nobody"
        assert config.tmpfs_size == "200m"
    
    def test_to_dict(self):
        """测试转换为字典"""
        config = SecurityConfig(
            read_only=True,
            no_network=True,
            drop_all_capabilities=True,
            user="nobody",
            tmpfs_size="200m"
        )
        result = config.to_dict()
        
        assert result["read_only"] is True
        assert result["network_disabled"] is True
        assert result["network_mode"] == "none"
        assert result["cap_drop"] == ["ALL"]
        assert result["user"] == "nobody"
        assert "/tmp" in result["tmpfs"]
        assert "size=200m" in result["tmpfs"]["/tmp"]


class TestContainerConfig:
    """测试容器配置"""
    
    def test_container_config_creation(self):
        """测试容器配置创建"""
        resource_limits = ResourceLimits(memory="512m")
        security_config = SecurityConfig(user="nobody")
        
        config = ContainerConfig(
            image="python:3.11-slim",
            command=["python", "--version"],
            working_dir="/app",
            environment={"PYTHONPATH": "/app"},
            volumes={"/app": {"bind": "/app", "mode": "ro"}},
            resource_limits=resource_limits,
            security_config=security_config,
            timeout=60
        )
        
        assert config.image == "python:3.11-slim"
        assert config.command == ["python", "--version"]
        assert config.working_dir == "/app"
        assert config.environment["PYTHONPATH"] == "/app"
        assert config.volumes["/app"]["mode"] == "ro"
        assert config.resource_limits.memory == "512m"
        assert config.security_config.user == "nobody"
        assert config.timeout == 60
    
    def test_to_dict(self):
        """测试转换为字典"""
        config = ContainerConfig(
            image="python:3.11-slim",
            command=["python", "--version"]
        )
        
        result = config.to_dict()
        
        assert result["image"] == "python:3.11-slim"
        assert result["command"] == ["python", "--version"]
        assert result["detach"] is True
        assert result["remove"] is False


class TestContainerPool:
    """测试容器池"""
    
    def setup_method(self):
        """测试前设置"""
        self.pool = ContainerPool(max_pool_size=5, idle_timeout=60)
    
    def teardown_method(self):
        """测试后清理"""
        self.pool.stop_cleanup_task()
    
    def test_add_container(self):
        """测试添加容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper = ContainerWrapper(
            container=mock_container,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        self.pool.add_container(wrapper)
        
        assert self.pool.get_container_count() == 1
        assert "test-container-id" in self.pool._pool
        assert "python" in self.pool._language_pools
        assert "test-container-id" in self.pool._language_pools["python"]
    
    def test_remove_container(self):
        """测试移除容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper = ContainerWrapper(
            container=mock_container,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        self.pool.add_container(wrapper)
        assert self.pool.get_container_count() == 1
        
        removed_wrapper = self.pool.remove_container("test-container-id")
        assert removed_wrapper == wrapper
        assert self.pool.get_container_count() == 0
        assert "python" not in self.pool._language_pools
    
    def test_get_available_container(self):
        """测试获取可用容器"""
        mock_container1 = Mock()
        mock_container1.id = "container-1"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper1 = ContainerWrapper(
            container=mock_container1,
            config=config,
            created_at=time.time() - 100,
            last_used=time.time() - 50,  # 超过idle_timeout
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        mock_container2 = Mock()
        mock_container2.id = "container-2"
        wrapper2 = ContainerWrapper(
            container=mock_container2,
            config=config,
            created_at=time.time(),
            last_used=time.time(),  # 未超时
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        self.pool.add_container(wrapper1)
        self.pool.add_container(wrapper2)
        
        # 应该获取container-2，因为它未超时且可用
        available = self.pool.get_available_container("python")
        assert available == wrapper2
        
        # 获取指定语言的容器
        available = self.pool.get_available_container("javascript")
        assert available is None
    
    def test_cleanup_idle_containers(self):
        """测试清理空闲容器"""
        mock_container1 = Mock()
        mock_container1.id = "container-1"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper1 = ContainerWrapper(
            container=mock_container1,
            config=config,
            created_at=time.time() - 100,
            last_used=time.time() - 70,  # 超过idle_timeout (60s)
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        mock_container2 = Mock()
        mock_container2.id = "container-2"
        wrapper2 = ContainerWrapper(
            container=mock_container2,
            config=config,
            created_at=time.time(),
            last_used=time.time() - 30,  # 未超时
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        self.pool.add_container(wrapper1)
        self.pool.add_container(wrapper2)
        
        # 应该清理container-1
        containers_to_remove = self.pool.cleanup_idle_containers()
        assert "container-1" in containers_to_remove
        assert "container-2" not in containers_to_remove
        
        assert self.pool.get_container_count() == 1


class TestContainerManager:
    """测试容器管理器"""
    
    def setup_method(self):
        """测试前设置"""
        with patch('app.docker.container_manager.docker.from_env') as mock_docker:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_docker.return_value = mock_client
            
            self.manager = ContainerManager(max_pool_size=3, idle_timeout=60)
            self.mock_client = mock_client
    
    def test_get_image_for_language(self):
        """测试获取语言镜像"""
        assert self.manager._get_image_for_language("python") == "python:3.11-slim"
        assert self.manager._get_image_for_language("javascript") == "node:18-alpine"
        assert self.manager._get_image_for_language("unknown") == "unknown"
    
    def test_get_resource_limits_for_language(self):
        """测试获取语言资源限制"""
        limits = self.manager._get_resource_limits_for_language("java")
        assert limits.memory == "512m"
        assert limits.cpu_quota == 100000
        
        limits = self.manager._get_resource_limits_for_language("unknown")
        assert limits.memory == "256m"
        assert limits.cpu_quota == 50000
    
    def test_create_container_config(self):
        """测试创建容器配置"""
        config = self.manager.create_container_config(
            language="python",
            command=["python", "--version"],
            environment={"PYTHONPATH": "/app"},
            custom_image="python:3.10-slim"
        )
        
        assert config.image == "python:3.10-slim"
        assert config.command == ["python", "--version"]
        assert config.environment["PYTHONPATH"] == "/app"
        assert config.resource_limits.memory == "256m"
    
    def test_create_container_success(self):
        """测试成功创建容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        mock_container.status = "created"
        self.mock_client.containers.create.return_value = mock_container
        
        config = ContainerConfig(image="python:3.11-slim")
        container = self.manager._create_container(config)
        
        assert container == mock_container
        self.mock_client.containers.create.assert_called_once()
    
    def test_create_container_image_not_found(self):
        """测试镜像未找到"""
        from docker.errors import ImageNotFound
        
        self.mock_client.containers.create.side_effect = ImageNotFound("Image not found")
        
        config = ContainerConfig(image="python:3.11-slim")
        
        with pytest.raises(ContainerCreationError, match="镜像未找到"):
            self.manager._create_container(config)
    
    def test_start_container_success(self):
        """测试成功启动容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        
        self.manager._start_container(mock_container)
        mock_container.start.assert_called_once()
    
    def test_start_container_failure(self):
        """测试启动容器失败"""
        from docker.errors import APIError
        
        mock_container = Mock()
        mock_container.id = "test-container-id"
        mock_container.start.side_effect = APIError("Start failed")
        
        with pytest.raises(ContainerCreationError, match="容器启动失败"):
            self.manager._start_container(mock_container)
    
    def test_stop_container_success(self):
        """测试成功停止容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        
        self.manager._stop_container(mock_container)
        mock_container.stop.assert_called_once_with(timeout=10)
    
    def test_remove_container_success(self):
        """测试成功删除容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        
        self.manager._remove_container(mock_container)
        mock_container.remove.assert_called_once_with(force=True)
    
    def test_create_and_start_container(self):
        """测试创建并启动容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        mock_container.status = "running"
        
        self.mock_client.containers.create.return_value = mock_container
        
        container_id = self.manager.create_and_start_container(
            language="python",
            command=["python", "--version"]
        )
        
        assert container_id == "test-container-id"
        self.mock_client.containers.create.assert_called_once()
        mock_container.start.assert_called_once()
    
    def test_create_and_start_container_pool_full(self):
        """测试容器池已满"""
        # 模拟容器池已满
        for i in range(self.manager.max_pool_size):
            mock_container = Mock()
            mock_container.id = f"container-{i}"
            mock_container.status = "running"
            
            config = ContainerConfig(image="python:3.11-slim")
            wrapper = ContainerWrapper(
                container=mock_container,
                config=config,
                created_at=time.time(),
                last_used=time.time(),
                status=ContainerStatus.RUNNING,
                language="python"
            )
            self.manager._pool.add_container(wrapper)
        
        with pytest.raises(ContainerManagerError, match="容器池已满"):
            self.manager.create_and_start_container(language="python")
    
    def test_get_container_for_language(self):
        """测试获取指定语言的容器"""
        mock_container = Mock()
        mock_container.id = "test-container-id"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper = ContainerWrapper(
            container=mock_container,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        self.manager._pool.add_container(wrapper)
        
        container_id = self.manager.get_container_for_language("python")
        assert container_id == "test-container-id"
        assert wrapper.in_use is True
    
    def test_get_pool_status(self):
        """测试获取容器池状态"""
        mock_container1 = Mock()
        mock_container1.id = "container-1"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper1 = ContainerWrapper(
            container=mock_container1,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.RUNNING,
            language="python",
            in_use=True
        )
        
        mock_container2 = Mock()
        mock_container2.id = "container-2"
        wrapper2 = ContainerWrapper(
            container=mock_container2,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.RUNNING,
            language="javascript",
            in_use=False
        )
        
        self.manager._pool.add_container(wrapper1)
        self.manager._pool.add_container(wrapper2)
        
        status = self.manager.get_pool_status()
        
        assert status["total_containers"] == 2
        assert status["in_use_containers"] == 1
        assert status["available_containers"] == 1
        assert status["language_distribution"]["python"] == 1
        assert status["language_distribution"]["javascript"] == 1
        assert status["max_pool_size"] == 3
        assert status["idle_timeout"] == 60
    
    def test_cleanup_all_containers(self):
        """测试清理所有容器"""
        mock_container1 = Mock()
        mock_container1.id = "container-1"
        mock_container1.status = "running"
        
        mock_container2 = Mock()
        mock_container2.id = "container-2"
        mock_container2.status = "exited"
        
        config = ContainerConfig(image="python:3.11-slim")
        wrapper1 = ContainerWrapper(
            container=mock_container1,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.RUNNING,
            language="python"
        )
        
        wrapper2 = ContainerWrapper(
            container=mock_container2,
            config=config,
            created_at=time.time(),
            last_used=time.time(),
            status=ContainerStatus.EXITED,
            language="javascript"
        )
        
        self.manager._pool.add_container(wrapper1)
        self.manager._pool.add_container(wrapper2)
        
        self.manager.cleanup_all_containers()
        
        # 验证容器已被停止和删除
        mock_container1.stop.assert_called_once()
        mock_container1.remove.assert_called_once()
        mock_container2.remove.assert_called_once()
        
        # 验证池已清空
        assert len(self.manager._pool._pool) == 0


if __name__ == "__main__":
    pytest.main([__file__])