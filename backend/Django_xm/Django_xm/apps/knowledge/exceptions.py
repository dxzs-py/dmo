"""知识库模块业务异常。"""

from Django_xm.common.exceptions import BaseAppError


class KnowledgeBaseError(BaseAppError):
    """知识库业务异常基类。"""


class KnowledgeBaseAlreadyExistsError(KnowledgeBaseError):
    """知识库已存在（活跃同名知识库，无法重复创建）。

    Attributes:
        name: 触发冲突的知识库名称
    """

    def __init__(self, message: str, name: str):
        super().__init__(message)
        self.name = name
