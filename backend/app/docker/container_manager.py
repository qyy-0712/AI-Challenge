"""
Docker容器管理器

提供Docker容器的创建、管理、监控和清理功能，实现线程安全的容器池管理和复用机制。
"""

import asyncio
import logging
import time
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Any, AsyncGenerator
import uuid

import docker
from docker.errors import DockerException, ContainerError, ImageNotFound, APIError
from docker.models.containers import Container


# 配置日志
logger = logging.getLogger(__name__)


class ContainerStatus(Enum):
    """容器状态枚举"""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESTARTING = "restarting"
    REMOVING = "removing"
    EXITED = "exited"
    DEAD = "dead"


@dataclass
class ResourceLimits:
    """容器资源限制配置"""
    memory: str = "256m"  # 内存限制，默认256MB
    cpu_quota: int = 50000  # CPU配额，默认50000（0.5核）
    cpu_period: int = 100000  # CPU周期，默认100000
    blkio_weight: int = 0  # 块IO权重
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为Docker API字典格式"""
        limits = {}
        if self.memory:
            limits["mem_limit"] = self.memory
        if self.cpu_quota:
            limits["cpu_quota"] = self.cpu_quota
        if self.cpu_period:
            limits["cpu_period"] = self.cpu_period
        if self.blkio_weight:
            limits["blkio_weight"] = self.blkio_weight
        return limits


@dataclass
class SecurityConfig:
    """容器安全配置"""
    read_only: bool = True  # 只读根文件系统
    no_network: bool = True  # 禁用网络
    drop_all_capabilities: bool = True  # 丢弃所有能力
    user: Optional[str] = None  # 非root用户
    tmpfs_size: str = "100m"  # 临时文件系统大小
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为Docker API字典格式"""
        config = {}
        
        if self.read_only:
            config["read_only"] = True
            
        if self.no_network:
            config["network_disabled"] = True
            config["network_mode"] = "none"
            
        if self.drop_all_capabilities:
            config["cap_drop"] = ["ALL"]
            
        if self.user:
            config["user"] = self.user
            
        # 临时文件系统配置
        tmpfs = {
            "/tmp": f"size={self.tmpfs_size},noexec,nosuid,nodev",
            "/var/tmp": f"size={self.tmpfs_size},noexec,nosuid,nodev"
        }
        config["tmpfs"] = tmpfs
        
        return config


@dataclass
class ContainerConfig:
    """容器配置"""
    image: str
    command: Optional[List[str]] = None
    working_dir: Optional[str] = None
    environment: Dict[str, str] = field(default_factory=dict)
    volumes: Dict[str, Dict[str, str]] = field(default_factory=dict)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    security_config: SecurityConfig = field(default_factory=SecurityConfig)
    timeout: int = 30  # 默认超时时间（秒）
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为Docker API字典格式"""
        config = {
            "image": self.image,
            "detach": True,
            "remove": False,  # 不自动删除，由容器池管理
        }
        
        if self.command:
            config["command"] = self.command
            
        if self.working_dir:
            config["working_dir"] = self.working_dir
            
        if self.environment:
            config["environment"] = self.environment
            
        if self.volumes:
            config["volumes"] = self.volumes
            
        # 合并资源限制
        config.update(self.resource_limits.to_dict())
        
        # 合并安全配置
        config.update(self.security_config.to_dict())
        
        return config


@dataclass
class ContainerWrapper:
    """容器包装器，包含容器实例和元数据"""
    container: Container
    config: ContainerConfig
    created_at: float
    last_used: float
    status: ContainerStatus
    in_use: bool = False
    language: Optional[str] = None  # 容器所属编程语言
    
    def update_last_used(self) -> None:
        """更新最后使用时间"""
        self.last_used = time.time()
    
    def mark_in_use(self, in_use: bool = True) -> None:
        """标记容器使用状态"""
        self.in_use = in_use
        if in_use:
            self.update_last_used()


class ContainerManagerError(Exception):
    """容器管理器基础异常"""
    pass


class ContainerCreationError(ContainerManagerError):
    """容器创建异常"""
    pass


class ContainerExecutionError(ContainerManagerError):
    """容器执行异常"""
    pass


class ContainerTimeoutError(ContainerManagerError):
    """容器执行超时异常"""
    pass


class ContainerPool:
    """容器池管理器，实现容器复用和生命周期管理"""
    
    def __init__(self, max_pool_size: int = 10, idle_timeout: int = 300):
        self.max_pool_size = max_pool_size
        self.idle_timeout = idle_timeout
        self._pool: Dict[str, ContainerWrapper] = {}
        self._language_pools: Dict[str, Set[str]] = {}
        self._lock = threading.RLock()
        self._cleanup_task: Optional[asyncio.Task] = None
        
    def get_container_count(self, language: Optional[str] = None) -> int:
        """获取容器数量"""
        with self._lock:
            if language:
                return len(self._language_pools.get(language, set()))
            return len(self._pool)
    
    def add_container(self, container_wrapper: ContainerWrapper) -> None:
        """添加容器到池中"""
        with self._lock:
            container_id = container_wrapper.container.id
            self._pool[container_id] = container_wrapper
            
            if container_wrapper.language:
                if container_wrapper.language not in self._language_pools:
                    self._language_pools[container_wrapper.language] = set()
                self._language_pools[container_wrapper.language].add(container_id)
                
            logger.info(f"容器 {container_id} 已添加到池中，语言: {container_wrapper.language}")
    
    def remove_container(self, container_id: str) -> Optional[ContainerWrapper]:
        """从池中移除容器"""
        with self._lock:
            wrapper = self._pool.pop(container_id, None)
            if wrapper:
                if wrapper.language and wrapper.language in self._language_pools:
                    self._language_pools[wrapper.language].discard(container_id)
                logger.info(f"容器 {container_id} 已从池中移除")
            return wrapper
    
    def get_available_container(self, language: Optional[str] = None) -> Optional[ContainerWrapper]:
        """获取可用的容器"""
        with self._lock:
            current_time = time.time()
            
            # 优先获取指定语言的容器
            if language and language in self._language_pools:
                for container_id in self._language_pools[language]:
                    wrapper = self._pool.get(container_id)
                    if (wrapper and not wrapper.in_use and 
                        wrapper.status == ContainerStatus.RUNNING and
                        current_time - wrapper.last_used < self.idle_timeout):
                        return wrapper
            
            # 如果没有指定语言的容器，获取任何可用容器
            for wrapper in self._pool.values():
                if (not wrapper.in_use and 
                    wrapper.status == ContainerStatus.RUNNING and
                    current_time - wrapper.last_used < self.idle_timeout):
                    return wrapper
                    
            return None
    
    def cleanup_idle_containers(self) -> List[str]:
        """清理空闲容器"""
        with self._lock:
            current_time = time.time()
            containers_to_remove = []
            
            for container_id, wrapper in list(self._pool.items()):
                if (not wrapper.in_use and 
                    current_time - wrapper.last_used > self.idle_timeout):
                    containers_to_remove.append(container_id)
                    
            for container_id in containers_to_remove:
                self.remove_container(container_id)
                
            return containers_to_remove
    
    def start_cleanup_task(self) -> None:
        """启动清理任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            logger.info("容器池清理任务已启动")
    
    async def _periodic_cleanup(self) -> None:
        """定期清理空闲容器"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                containers_to_remove = self.cleanup_idle_containers()
                if containers_to_remove:
                    logger.info(f"清理 {len(containers_to_remove)} 个空闲容器")
            except asyncio.CancelledError:
                logger.info("容器池清理任务已取消")
                break
            except Exception as e:
                logger.error(f"容器池清理出错: {e}")
    
    def stop_cleanup_task(self) -> None:
        """停止清理任务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("容器池清理任务已停止")


class ContainerManager:
    """Docker容器管理器"""
    
    # 默认语言镜像映射
    DEFAULT_LANGUAGE_IMAGES = {
        "python": "python:3.11-slim",
        "javascript": "node:18-alpine",
        "typescript": "node:18-alpine",
        "java": "openjdk:17-slim",
        "c": "gcc:latest",
        "cpp": "gcc:latest",
        "go": "golang:1.21-alpine",
        "rust": "rust:1.75-slim",
        "php": "php:8.2-cli",
        "ruby": "ruby:3.2-slim",
    }
    
    # 默认资源限制
    DEFAULT_RESOURCE_LIMITS = {
        "python": ResourceLimits(memory="256m", cpu_quota=50000),
        "javascript": ResourceLimits(memory="256m", cpu_quota=50000),
        "typescript": ResourceLimits(memory="384m", cpu_quota=50000),
        "java": ResourceLimits(memory="512m", cpu_quota=100000),
        "c": ResourceLimits(memory="256m", cpu_quota=50000),
        "cpp": ResourceLimits(memory="256m", cpu_quota=50000),
        "go": ResourceLimits(memory="256m", cpu_quota=50000),
        "rust": ResourceLimits(memory="512m", cpu_quota=100000),
        "php": ResourceLimits(memory="256m", cpu_quota=50000),
        "ruby": ResourceLimits(memory="256m", cpu_quota=50000),
    }
    
    def __init__(self, 
                 max_pool_size: int = 10, 
                 idle_timeout: int = 300,
                 default_timeout: int = 30):
        self.max_pool_size = max_pool_size
        self.idle_timeout = idle_timeout
        self.default_timeout = default_timeout
        self._client = None
        self._pool = ContainerPool(max_pool_size, idle_timeout)
        self._lock = threading.RLock()
        
    @property
    def client(self) -> docker.DockerClient:
        """获取Docker客户端"""
        if self._client is None:
            try:
                self._client = docker.from_env()
                # 测试连接
                self._client.ping()
                logger.info("Docker客户端连接成功")
            except DockerException as e:
                logger.error(f"Docker客户端连接失败: {e}")
                raise ContainerManagerError(f"无法连接到Docker: {e}")
        return self._client
    
    def _get_image_for_language(self, language: str) -> str:
        """获取语言对应的Docker镜像"""
        return self.DEFAULT_LANGUAGE_IMAGES.get(language.lower(), language)
    
    def _get_resource_limits_for_language(self, language: str) -> ResourceLimits:
        """获取语言对应的资源限制"""
        return self.DEFAULT_RESOURCE_LIMITS.get(language.lower(), ResourceLimits())
    
    def create_container_config(self,
                              language: str,
                              command: Optional[List[str]] = None,
                              environment: Optional[Dict[str, str]] = None,
                              volumes: Optional[Dict[str, Dict[str, str]]] = None,
                              custom_image: Optional[str] = None,
                              custom_resource_limits: Optional[ResourceLimits] = None) -> ContainerConfig:
        """创建容器配置"""
        image = custom_image or self._get_image_for_language(language)
        resource_limits = custom_resource_limits or self._get_resource_limits_for_language(language)
        
        return ContainerConfig(
            image=image,
            command=command,
            environment=environment or {},
            volumes=volumes or {},
            resource_limits=resource_limits,
            timeout=self.default_timeout
        )
    
    def _create_container(self, config: ContainerConfig) -> Container:
        """创建Docker容器"""
        try:
            logger.info(f"正在创建容器，镜像: {config.image}")
            container_config = config.to_dict()
            container = self.client.containers.create(**container_config)
            logger.info(f"容器创建成功，ID: {container.id}")
            return container
        except ImageNotFound:
            logger.error(f"镜像未找到: {config.image}")
            raise ContainerCreationError(f"镜像未找到: {config.image}")
        except APIError as e:
            logger.error(f"容器创建失败: {e}")
            raise ContainerCreationError(f"容器创建失败: {e}")
    
    def _start_container(self, container: Container) -> None:
        """启动容器"""
        try:
            logger.info(f"正在启动容器，ID: {container.id}")
            container.start()
            logger.info(f"容器启动成功，ID: {container.id}")
        except APIError as e:
            logger.error(f"容器启动失败: {e}")
            raise ContainerCreationError(f"容器启动失败: {e}")
    
    def _stop_container(self, container: Container, timeout: int = 10) -> None:
        """停止容器"""
        try:
            logger.info(f"正在停止容器，ID: {container.id}")
            container.stop(timeout=timeout)
            logger.info(f"容器停止成功，ID: {container.id}")
        except APIError as e:
            logger.warning(f"容器停止失败: {e}")
    
    def _remove_container(self, container: Container, force: bool = True) -> None:
        """删除容器"""
        try:
            logger.info(f"正在删除容器，ID: {container.id}")
            container.remove(force=force)
            logger.info(f"容器删除成功，ID: {container.id}")
        except APIError as e:
            logger.warning(f"容器删除失败: {e}")
    
    def _execute_command(self, 
                        container: Container, 
                        command: List[str], 
                        timeout: Optional[int] = None) -> tuple:
        """在容器中执行命令"""
        try:
            logger.info(f"在容器 {container.id} 中执行命令: {' '.join(command)}")
            
            # 设置超时
            exec_timeout = timeout or self.default_timeout
            
            # 执行命令
            result = container.run(
                command,
                stdout=True,
                stderr=True,
                demux=True,
                timeout=exec_timeout
            )
            
            logger.info(f"命令执行完成，ID: {container.id}")
            return result.exit_code, result.output, result.errors
            
        except ContainerError as e:
            logger.error(f"容器执行命令失败: {e}")
            raise ContainerExecutionError(f"容器执行命令失败: {e}")
        except Exception as e:
            if "timeout" in str(e).lower():
                logger.error(f"容器执行命令超时: {e}")
                raise ContainerTimeoutError(f"容器执行命令超时: {e}")
            logger.error(f"容器执行命令异常: {e}")
            raise ContainerExecutionError(f"容器执行命令异常: {e}")
    
    def create_and_start_container(self,
                                 language: str,
                                 command: Optional[List[str]] = None,
                                 environment: Optional[Dict[str, str]] = None,
                                 volumes: Optional[Dict[str, Dict[str, str]]] = None,
                                 custom_image: Optional[str] = None,
                                 custom_resource_limits: Optional[ResourceLimits] = None) -> str:
        """创建并启动容器"""
        with self._lock:
            # 检查容器池大小
            if len(self._pool._pool) >= self.max_pool_size:
                # 清理一些空闲容器
                self._pool.cleanup_idle_containers()
                
                if len(self._pool._pool) >= self.max_pool_size:
                    raise ContainerManagerError("容器池已满，无法创建新容器")
            
            # 创建容器配置
            config = self.create_container_config(
                language=language,
                command=command,
                environment=environment,
                volumes=volumes,
                custom_image=custom_image,
                custom_resource_limits=custom_resource_limits
            )
            
            # 创建容器
            container = self._create_container(config)
            
            # 启动容器
            self._start_container(container)
            
            # 创建包装器
            wrapper = ContainerWrapper(
                container=container,
                config=config,
                created_at=time.time(),
                last_used=time.time(),
                status=ContainerStatus.RUNNING,
                language=language
            )
            
            # 添加到容器池
            self._pool.add_container(wrapper)
            
            return container.id
    
    def get_container_for_language(self, language: str) -> Optional[str]:
        """获取指定语言的容器"""
        with self._lock:
            wrapper = self._pool.get_available_container(language)
            if wrapper:
                wrapper.mark_in_use(True)
                return wrapper.container.id
            return None
    
    def execute_in_container(self, 
                           container_id: str, 
                           command: List[str], 
                           timeout: Optional[int] = None) -> tuple:
        """在指定容器中执行命令"""
        with self._lock:
            wrapper = self._pool._pool.get(container_id)
            if not wrapper:
                raise ContainerManagerError(f"容器不存在: {container_id}")
            
            if not wrapper.in_use:
                wrapper.mark_in_use(True)
            
            try:
                # 执行命令
                exit_code, stdout, stderr = self._execute_command(
                    wrapper.container, command, timeout
                )
                
                # 更新最后使用时间
                wrapper.update_last_used()
                
                return exit_code, stdout, stderr
                
            finally:
                wrapper.mark_in_use(False)
    
    def return_container_to_pool(self, container_id: str) -> None:
        """将容器返回到池中"""
        with self._lock:
            wrapper = self._pool._pool.get(container_id)
            if wrapper:
                wrapper.mark_in_use(False)
                wrapper.update_last_used()
                logger.info(f"容器 {container_id} 已返回到池中")
    
    def remove_container(self, container_id: str) -> None:
        """删除容器"""
        with self._lock:
            wrapper = self._pool.remove_container(container_id)
            if wrapper:
                try:
                    # 停止容器
                    if wrapper.container.status == "running":
                        self._stop_container(wrapper.container)
                    
                    # 删除容器
                    self._remove_container(wrapper.container)
                    
                except Exception as e:
                    logger.error(f"删除容器 {container_id} 失败: {e}")
    
    def cleanup_all_containers(self) -> None:
        """清理所有容器"""
        with self._lock:
            container_ids = list(self._pool._pool.keys())
            for container_id in container_ids:
                self.remove_container(container_id)
            
            logger.info("所有容器已清理")
    
    def start_pool_management(self) -> None:
        """启动容器池管理"""
        self._pool.start_cleanup_task()
        logger.info("容器池管理已启动")
    
    def stop_pool_management(self) -> None:
        """停止容器池管理"""
        self._pool.stop_cleanup_task()
        self.cleanup_all_containers()
        logger.info("容器池管理已停止")
    
    def get_pool_status(self) -> Dict[str, Any]:
        """获取容器池状态"""
        with self._lock:
            total_containers = len(self._pool._pool)
            language_counts = {}
            in_use_count = 0
            
            for wrapper in self._pool._pool.values():
                if wrapper.language:
                    language_counts[wrapper.language] = language_counts.get(wrapper.language, 0) + 1
                if wrapper.in_use:
                    in_use_count += 1
            
            return {
                "total_containers": total_containers,
                "in_use_containers": in_use_count,
                "available_containers": total_containers - in_use_count,
                "language_distribution": language_counts,
                "max_pool_size": self.max_pool_size,
                "idle_timeout": self.idle_timeout
            }
    
    @asynccontextmanager
    async def get_container(self,
                          language: str,
                          command: Optional[List[str]] = None,
                          environment: Optional[Dict[str, str]] = None,
                          volumes: Optional[Dict[str, Dict[str, str]]] = None,
                          custom_image: Optional[str] = None,
                          custom_resource_limits: Optional[ResourceLimits] = None) -> AsyncGenerator[str, None]:
        """获取容器的异步上下文管理器"""
        container_id = None
        
        try:
            # 尝试从池中获取现有容器
            container_id = self.get_container_for_language(language)
            
            # 如果没有可用容器，创建新容器
            if not container_id:
                container_id = self.create_and_start_container(
                    language=language,
                    command=command,
                    environment=environment,
                    volumes=volumes,
                    custom_image=custom_image,
                    custom_resource_limits=custom_resource_limits
                )
            
            yield container_id
            
        finally:
            if container_id:
                self.return_container_to_pool(container_id)