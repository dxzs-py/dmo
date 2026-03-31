from django.db import models


class ChatSession(models.Model):
    session_id = models.CharField(
        max_length=100, 
        unique=True, 
        db_index=True,
        verbose_name='会话ID'
    )
    created_at = models.DateTimeField(
        auto_now_add=True, 
        db_index=True,
        verbose_name='创建时间'
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name='更新时间'
    )

    class Meta:
        db_table = 'chat_session'
        verbose_name = '聊天会话'
        verbose_name_plural = '聊天会话'
        indexes = [
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"ChatSession {self.session_id}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('chat:chat')


class ChatMessage(models.Model):
    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_SYSTEM = 'system'
    
    ROLE_CHOICES = [
        (ROLE_USER, '用户'),
        (ROLE_ASSISTANT, '助手'),
        (ROLE_SYSTEM, '系统'),
    ]
    
    session = models.ForeignKey(
        ChatSession, 
        on_delete=models.CASCADE, 
        related_name='messages',
        db_index=True,
        verbose_name='会话'
    )
    role = models.CharField(
        max_length=20, 
        choices=ROLE_CHOICES,
        default=ROLE_USER,
        db_index=True,
        verbose_name='角色'
    )
    content = models.TextField(verbose_name='内容')
    created_at = models.DateTimeField(
        auto_now_add=True, 
        db_index=True,
        verbose_name='创建时间'
    )

    class Meta:
        db_table = 'chat_message'
        verbose_name = '聊天消息'
        verbose_name_plural = '聊天消息'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['session', 'created_at']),
            models.Index(fields=['role']),
        ]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('chat:chat')
