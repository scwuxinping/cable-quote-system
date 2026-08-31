from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'),
         name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('calc/', views.calc, name='calc'),
    path('specs/', views.spec_list, name='spec_list'),
    path('prices/', views.prices, name='prices'),
    path('quotes/', views.quote_list, name='quote_list'),
    path('quotes/new/', views.quote_new, name='quote_new'),
    path('quotes/<int:pk>/', views.quote_detail, name='quote_detail'),
    path('quotes/<int:pk>/submit/', views.quote_submit, name='quote_submit'),
    path('quotes/<int:pk>/approve/', views.quote_approve, name='quote_approve'),
    path('quotes/<int:pk>/reject/', views.quote_reject, name='quote_reject'),
    path('quotes/<int:pk>/delete/', views.quote_delete, name='quote_delete'),
    path('quotes/<int:pk>/export/', views.quote_export, name='quote_export'),
    path('quotes/<int:pk>/send/', views.quote_send, name='quote_send'),
    path('quotes/<int:pk>/to-order/', views.quote_to_order, name='quote_to_order'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('orders/<int:pk>/status/', views.order_status, name='order_status'),
    path('orders/<int:pk>/payment/', views.payment_add, name='payment_add'),
    path('import/', views.import_quotes, name='import_quotes'),
    path('import/create/', views.import_create, name='import_create'),
    path('import/template/', views.import_template, name='import_template'),
    path('api/specs/', views.api_specs, name='api_specs'),
    path('api/tiers/', views.api_tiers, name='api_tiers'),
]
