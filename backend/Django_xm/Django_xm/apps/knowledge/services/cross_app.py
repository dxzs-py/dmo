"""
Knowledge 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 knowledge.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""

def get_index_manager():
    """供其他应用调用：获取索引管理器实例"""
    from .index_service import IndexManager

    return IndexManager()
