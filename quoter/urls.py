from django.contrib.auth import views as auth_views
from django.urls import path

from . import portal, views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'),
         name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    # 客户门户（免登录）
    path('portal/', portal.portal_home, name='portal_home'),
    path('portal/success/', portal.portal_success, name='portal_success'),
    path('portal/price/', portal.portal_price, name='portal_price'),
    path('q/<str:token>/', portal.portal_quote, name='portal_quote'),
    path('q/<str:token>/sign/', portal.portal_sign, name='portal_sign'),
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
    path('quotes/<int:pk>/share/', views.quote_share, name='quote_share'),
    path('leads/', views.lead_list, name='lead_list'),
    path('leads/<int:pk>/to-quote/', views.lead_to_quote, name='lead_to_quote'),
    path('quotes/<int:pk>/to-order/', views.quote_to_order, name='quote_to_order'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('orders/<int:pk>/status/', views.order_status, name='order_status'),
    path('orders/<int:pk>/payment/', views.payment_add, name='payment_add'),
    path('orders/<int:pk>/export/', views.order_export, name='order_export'),
    path('import/', views.import_quotes, name='import_quotes'),
    path('import/create/', views.import_create, name='import_create'),
    path('import/template/', views.import_template, name='import_template'),
    path('api/specs/', views.api_specs, name='api_specs'),
    path('api/tiers/', views.api_tiers, name='api_tiers'),
]
