from django.urls import path
from . import views

urlpatterns = [
    path('query/', views.RagQueryView.as_view(), name='rag-query'),
    path('indexes/', views.DocumentIndexListView.as_view(), name='rag-indexes'),
    path('indexes/<int:index_id>/documents/', views.DocumentListView.as_view(), name='rag-documents'),
]
