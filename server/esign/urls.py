from django.urls import path
from . import views

urlpatterns = [
    path('', views.esign_list, name='esign_list'),
    path('new/', views.esign_create, name='esign_create'),
    path('<int:pk>/prepare/', views.esign_prepare, name='esign_prepare'),
    path('<int:pk>/send/', views.esign_send, name='esign_send'),
    path('<int:pk>/delete/', views.esign_delete, name='esign_delete'),
    # public signer routes (no login)
    path('s/<uuid:token>/', views.esign_public_sign, name='esign_public_sign'),
    path('s/<uuid:token>/submit/', views.esign_public_submit, name='esign_public_submit'),
    path('s/<uuid:token>/download/', views.esign_download, name='esign_download'),
]
