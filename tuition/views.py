from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum, F, Window
from django.db.models.functions import Rank
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from .models import Student, Attendance, Exam, MarkEntry, ClassGroup

def landing_page_view(request):
    return render(request, 'tuition/landing.html')

def parent_login_view(request):
    if request.method == 'POST':
        student_id = request.POST.get('student_id')
        pin = request.POST.get('pin')
        
        try:
            student = Student.objects.get(student_id=student_id, pin=pin)
            # Store student ID in session
            request.session['logged_in_student'] = student.id
            return redirect('parent_dashboard')
        except Student.DoesNotExist:
            messages.error(request, 'Invalid Student ID or PIN.')
            
    return render(request, 'tuition/parent_login.html')

def parent_logout_view(request):
    if 'logged_in_student' in request.session:
        del request.session['logged_in_student']
    return redirect('parent_login')

def parent_dashboard_view(request):
    student_id = request.session.get('logged_in_student')
    if not student_id:
        return redirect('parent_login')
        
    student = get_object_or_404(Student, id=student_id)
    class_group = student.class_group
    
    # 1. Attendance calculation
    total_days = Attendance.objects.filter(student=student).count()
    present_days = Attendance.objects.filter(student=student, status='present').count()
    attendance_percentage = (present_days / total_days * 100) if total_days > 0 else 0
    
    # 2. Performance chart data (for this student)
    marks = MarkEntry.objects.filter(student=student).order_by('exam__date')
    labels = [mark.exam.name for mark in marks]
    data = [float(mark.percentage) for mark in marks]
    
    # 3. Leaderboard Calculation
    # We want to rank students in the same class group based on total marks obtained across all exams
    students_in_class = Student.objects.filter(class_group=class_group)
    
    leaderboard_data = []
    for s in students_in_class:
        total_marks = MarkEntry.objects.filter(student=s).aggregate(total=Sum('marks_obtained'))['total'] or 0
        total_max = MarkEntry.objects.filter(student=s).aggregate(max_total=Sum('exam__max_marks'))['max_total'] or 0
        
        leaderboard_data.append({
            'student_name': s.full_name,
            'is_current': s.id == student.id,
            'total_marks': float(total_marks),
            'total_max': float(total_max)
        })
        
    # Sort leaderboard by total_marks descending
    leaderboard_data.sort(key=lambda x: x['total_marks'], reverse=True)
    
    # Add ranks
    rank = 1
    for i, entry in enumerate(leaderboard_data):
        if i > 0 and entry['total_marks'] < leaderboard_data[i-1]['total_marks']:
            rank = i + 1
        entry['rank'] = rank

    context = {
        'student': student,
        'attendance_percentage': attendance_percentage,
        'chart_labels': labels,
        'chart_data': data,
        'leaderboard': leaderboard_data
    }
    return render(request, 'tuition/parent_dashboard.html', context)


# --- Teacher / Admin Views ---

def teacher_login_view(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            if user.is_superuser or (hasattr(user, 'profile') and user.profile.role == 'admin'):
                return redirect('admin_dashboard')
            return redirect('teacher_dashboard')
        else:
            messages.error(request, 'Invalid Username or Password.')
    return render(request, 'tuition/teacher_login.html')

@login_required
def teacher_logout_view(request):
    logout(request)
    return redirect('teacher_login')

@login_required
def teacher_dashboard_view(request):
    classes = ClassGroup.objects.all()
    if hasattr(request.user, 'profile') and request.user.profile.role == 'teacher':
        classes = ClassGroup.objects.filter(teacher=request.user)
    
    return render(request, 'tuition/teacher_dashboard.html', {'classes': classes})

@login_required
def mark_attendance_view(request, class_id):
    class_group = get_object_or_404(ClassGroup, id=class_id)
    students = class_group.students.all()
    
    if request.method == 'POST':
        date = request.POST.get('date')
        for student in students:
            status = request.POST.get(f'status_{student.id}')
            if status:
                Attendance.objects.update_or_create(
                    student=student,
                    date=date,
                    defaults={'status': status, 'marked_by': request.user}
                )
        messages.success(request, f'Attendance marked for {date}.')
        return redirect('teacher_dashboard')
        
    return render(request, 'tuition/mark_attendance.html', {'class_group': class_group, 'students': students})

@login_required
def enter_marks_view(request, class_id):
    class_group = get_object_or_404(ClassGroup, id=class_id)
    exams = class_group.exams.all()
    students = class_group.students.all()
    
    if request.method == 'POST':
        exam_id = request.POST.get('exam_id')
        exam = get_object_or_404(Exam, id=exam_id)
        
        for student in students:
            marks = request.POST.get(f'marks_{student.id}')
            if marks:
                MarkEntry.objects.update_or_create(
                    student=student,
                    exam=exam,
                    defaults={'marks_obtained': marks, 'entered_by': request.user}
                )
        messages.success(request, f'Marks entered for {exam.name}.')
        return redirect('teacher_dashboard')
        
    return render(request, 'tuition/enter_marks.html', {'class_group': class_group, 'exams': exams, 'students': students})


# --- Admin Portal Views ---

def is_admin(user):
    return user.is_superuser or (hasattr(user, 'profile') and user.profile.role == 'admin')

@login_required
@user_passes_test(is_admin, login_url='/teacher/')
def admin_dashboard_view(request):
    today = timezone.now().date()
    total_students = Student.objects.filter(is_active=True).count()
    
    # Calculate attendance for today
    today_attendances = Attendance.objects.filter(date=today)
    total_present = today_attendances.filter(status='present').count()
    total_absent = today_attendances.filter(status='absent').count()
    
    context = {
        'total_students': total_students,
        'total_present': total_present,
        'total_absent': total_absent,
    }
    return render(request, 'tuition/admin_dashboard.html', context)

@login_required
@user_passes_test(is_admin)
def admin_classes_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            ClassGroup.objects.create(name=name)
            messages.success(request, 'Class created successfully.')
        return redirect('admin_classes')
        
    classes = ClassGroup.objects.all()
    return render(request, 'tuition/admin_classes.html', {'classes': classes})

@login_required
@user_passes_test(is_admin)
def admin_students_view(request):
    if request.method == 'POST':
        student_id = request.POST.get('student_id')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        class_id = request.POST.get('class_id')
        pin = request.POST.get('pin')
        
        if all([student_id, first_name, last_name, class_id, pin]):
            class_group = get_object_or_404(ClassGroup, id=class_id)
            Student.objects.create(
                student_id=student_id,
                first_name=first_name,
                last_name=last_name,
                class_group=class_group,
                pin=pin
            )
            messages.success(request, 'Student added successfully.')
        return redirect('admin_students')
        
    students = Student.objects.all()
    classes = ClassGroup.objects.all()
    return render(request, 'tuition/admin_students.html', {'students': students, 'classes': classes})

@login_required
@user_passes_test(is_admin)
def admin_exams_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        class_id = request.POST.get('class_id')
        max_marks = request.POST.get('max_marks')
        
        if all([name, class_id, max_marks]):
            class_group = get_object_or_404(ClassGroup, id=class_id)
            Exam.objects.create(
                name=name,
                class_group=class_group,
                max_marks=max_marks,
                created_by=request.user
            )
            messages.success(request, 'Exam created successfully.')
        return redirect('admin_exams')
        
    exams = Exam.objects.all()
    classes = ClassGroup.objects.all()
    return render(request, 'tuition/admin_exams.html', {'exams': exams, 'classes': classes})
