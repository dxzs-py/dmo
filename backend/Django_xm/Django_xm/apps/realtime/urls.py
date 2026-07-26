"""realtime 子应用路由。"""

from django.urls import path

from .views import SnapshotView


app_name = 'realtime'

urlpatterns = [
    # 注：poll/ 路由已移除，由 snapshot/<session_id>/ 替代（spec.md RC4）
    path('snapshot/<str:session_id>/', SnapshotView.as_view(), name='realtime-snapshot'),
]
