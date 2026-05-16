from django.contrib import admin
from .models import UserProfile, ClassGroup, Student, Attendance, Exam, MarkEntry

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)

@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'teacher')

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'first_name', 'last_name', 'class_group', 'is_active')
    list_filter = ('class_group', 'is_active')
    search_fields = ('student_id', 'first_name', 'last_name')

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('student', 'date', 'status', 'marked_by')
    list_filter = ('date', 'status', 'student__class_group')

@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ('name', 'class_group', 'date', 'max_marks')
    list_filter = ('class_group', 'date')

@admin.register(MarkEntry)
class MarkEntryAdmin(admin.ModelAdmin):
    list_display = ('student', 'exam', 'marks_obtained', 'percentage')
    list_filter = ('exam__class_group', 'exam')
