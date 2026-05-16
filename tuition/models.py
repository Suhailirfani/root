from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('teacher', 'Teacher'),
        ('parent', 'Parent'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='parent')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


class ClassGroup(models.Model):
    """Represents a Class or Batch"""
    name = models.CharField(max_length=100, unique=True, help_text="e.g. Class 10 Science")
    description = models.TextField(blank=True)
    teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, limit_choices_to={'profile__role': 'teacher'}, related_name='managed_classes')

    def __str__(self):
        return self.name


class Student(models.Model):
    """Represents a Student"""
    student_id = models.CharField(max_length=50, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name='students')
    pin = models.CharField(max_length=4, help_text="4-digit PIN for Parent Login")
    parent_phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['class_group', 'first_name']

    def __str__(self):
        return f"{self.student_id} - {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Attendance(models.Model):
    """Daily Attendance for a Student"""
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='present')
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-date', 'student']
        unique_together = [['student', 'date']]

    def __str__(self):
        return f"{self.student} - {self.date} - {self.status}"


class Exam(models.Model):
    """Represents an Exam conducted for a ClassGroup"""
    name = models.CharField(max_length=100, help_text="e.g. Monthly Test 1")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name='exams')
    date = models.DateField(default=timezone.now)
    max_marks = models.DecimalField(max_digits=6, decimal_places=2, default=100)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-date', 'name']

    def __str__(self):
        return f"{self.name} - {self.class_group.name} ({self.date})"


class MarkEntry(models.Model):
    """Marks obtained by a student in an Exam"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='marks')
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='marks')
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    remarks = models.TextField(blank=True)
    entered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = [['student', 'exam']]
        ordering = ['-exam__date', '-marks_obtained']

    def __str__(self):
        return f"{self.student.first_name} - {self.exam.name} - {self.marks_obtained}/{self.exam.max_marks}"

    @property
    def percentage(self):
        if self.exam.max_marks > 0:
            return (self.marks_obtained / self.exam.max_marks) * 100
        return 0
