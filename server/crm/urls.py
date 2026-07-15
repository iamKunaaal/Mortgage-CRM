from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.CRMLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('search/', views.global_search, name='global_search'),

    path('leads/', views.lead_list, name='lead_list'),
    path('leads/new/', views.lead_create, name='lead_create'),
    path('leads/export/', views.lead_export, name='lead_export'),
    path('leads/bulk/', views.lead_bulk, name='lead_bulk'),
    path('leads/pipeline/', views.lead_pipeline, name='lead_pipeline'),
    path('leads/sources/', views.lead_sources, name='lead_sources'),
    path('leads/lost/', views.lost_leads, name='lost_leads'),
    path('leads/<int:pk>/', views.lead_detail, name='lead_detail'),
    path('leads/<int:pk>/edit/', views.lead_edit, name='lead_edit'),
    path('leads/<int:pk>/delete/', views.lead_delete, name='lead_delete'),
    path('leads/<int:pk>/stage/', views.lead_stage_update, name='lead_stage_update'),
    path('leads/<int:pk>/assign/', views.lead_assign, name='lead_assign'),
    path('leads/<int:pk>/disbursed-date/', views.lead_disbursed_date, name='lead_disbursed_date'),
    path('leads/<int:pk>/note/', views.lead_note_add, name='lead_note_add'),
    path('leads/<int:pk>/documents/upload/', views.lead_document_upload, name='lead_document_upload'),
    path('leads/<int:pk>/restore/', views.lead_restore, name='lead_restore'),
    path('leads/<int:pk>/pipeline-month/', views.lead_pipeline_month, name='lead_pipeline_month'),
    path('leads/sources/toggle/', views.source_toggle, name='source_toggle'),

    path('customization/', views.customization_list, name='customization_list'),
    path('customization/export/', views.customization_export, name='customization_export'),
    path('customization/add/<int:pk>/', views.customization_add, name='customization_add'),
    path('customization/<int:pk>/update/', views.customization_update, name='customization_update'),
    path('customization/<int:pk>/remove/', views.customization_remove, name='customization_remove'),

    path('tasks/', views.task_list, name='task_list'),
    path('tasks/overdue/', views.overdue_tasks, name='overdue_tasks'),
    path('tasks/new/', views.task_create, name='task_create'),
    path('tasks/export/', views.task_export, name='task_export'),
    path('tasks/<int:pk>/complete/', views.task_complete, name='task_complete'),
    path('tasks/<int:pk>/delete/', views.task_delete, name='task_delete'),

    path('banks/', views.bank_list, name='bank_list'),
    path('banks/new/', views.bank_create, name='bank_create'),
    path('banks/export/', views.bank_export, name='bank_export'),
    path('banks/<int:pk>/edit/', views.bank_edit, name='bank_edit'),
    path('banks/<int:pk>/toggle/', views.bank_toggle, name='bank_toggle'),
    path('banks/<int:pk>/delete/', views.bank_delete, name='bank_delete'),

    path('advisors/', views.advisor_list, name='advisor_list'),
    path('advisors/export/', views.advisor_export, name='advisor_export'),

    path('partners/', views.partner_list, name='partner_list'),
    path('partners/new/', views.partner_create, name='partner_create'),
    path('partners/export/', views.partner_export, name='partner_export'),
    path('partners/<int:pk>/delete/', views.partner_delete, name='partner_delete'),

    path('documents/', views.document_list, name='document_list'),
    path('documents/export/', views.document_export, name='document_export'),
    path('documents/<int:pk>/delete/', views.document_delete, name='document_delete'),
    path('documents/<int:pk>/<str:action>/', views.document_action, name='document_action'),

    path('finance/', views.finance, name='finance'),
    path('finance/export/', views.finance_export, name='finance_export'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.report_export, name='report_export'),

    path('users/', views.user_list, name='user_list'),
    path('users/new/', views.user_create, name='user_create'),
    path('users/export/', views.user_export, name='user_export'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),

    path('roles/', views.role_list, name='role_list'),
    path('roles/save/', views.role_perm_save, name='role_perm_save'),
    path('settings/', views.settings_view, name='settings_view'),
    path('settings/save/', views.settings_save, name='settings_save'),
    path('settings/state/', views.settings_state_save, name='settings_state_save'),
]
