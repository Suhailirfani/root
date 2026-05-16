from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing_page_view, name='landing_page'),
    path('login/', views.parent_login_view, name='parent_login'),
    path('dashboard/', views.parent_dashboard_view, name='parent_dashboard'),
    path('logout/', views.parent_logout_view, name='parent_logout'),
    
    path('teacher/', views.teacher_login_view, name='teacher_login'),
    path('teacher/dashboard/', views.teacher_dashboard_view, name='teacher_dashboard'),
    path('teacher/logout/', views.teacher_logout_view, name='teacher_logout'),
    path('teacher/class/<int:class_id>/attendance/', views.mark_attendance_view, name='mark_attendance'),
    path('teacher/class/<int:class_id>/marks/', views.enter_marks_view, name='enter_marks'),
    
    path('portal/dashboard/', views.admin_dashboard_view, name='admin_dashboard'),
    path('portal/classes/', views.admin_classes_view, name='admin_classes'),
    path('portal/students/', views.admin_students_view, name='admin_students'),
    path('portal/exams/', views.admin_exams_view, name='admin_exams'),
]
