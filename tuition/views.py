from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum, F, Window
from django.db.models.functions import Rank
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from .models import Student, Attendance, Exam, MarkEntry, ClassGroup, UserProfile, Subject, Centre, HomeTask, WorkingDay, HomeTaskCompletion
from django.contrib.auth.models import User
import csv
import random
import string
import json

from django.http import JsonResponse, HttpResponse
from django.template.loader import get_template
from io import BytesIO
try:
    from xhtml2pdf import pisa
except ImportError:
    pisa = None

def generate_pin(length=4):
    return ''.join(random.choices(string.digits, k=length))

from django.views.decorators.cache import never_cache

@never_cache
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
    logout(request)
    return redirect('landing_page')

def get_student_dashboard_context(student):
    class_group = student.class_group
    
    # 1. Attendance calculation
    # Total days should be based on the number of attendance records for the student
    total_days = Attendance.objects.filter(student=student).count()
    
    # Present and late both count towards positive attendance (or you can adjust this if late is half)
    present_days = Attendance.objects.filter(student=student, status__in=['present', 'late']).count()
    attendance_percentage = (present_days / total_days * 100) if total_days > 0 else 0
    
    # Today's attendance status
    today = timezone.now().date()
    today_attendance = Attendance.objects.filter(student=student, date=today).first()
    today_status = today_attendance.status if today_attendance else None
    
    # 2. Performance chart data (for this student)
    marks = MarkEntry.objects.filter(student=student).order_by('exam__date')
    labels = [f"{mark.exam.name} ({mark.subject.name})" if mark.subject else mark.exam.name for mark in marks]
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

    # 4. Upcoming Exams
    today = timezone.now().date()
    upcoming_exams = Exam.objects.filter(class_group=class_group, date__gte=today).order_by('date')[:5]
    today_tasks = HomeTask.objects.filter(class_group=class_group, date=today).order_by('-created_at')
    # Attach each task's completion for current student
    for task in today_tasks:
        task.completion = HomeTaskCompletion.objects.filter(task=task, student=student).first()

    # 5. Matrix Marks Grid (Detailed Mark List)
    subjects = class_group.subjects.all().order_by('name')
    
    exam_groups = {}
    for mark in marks:
        exam_id = mark.exam.id
        if exam_id not in exam_groups:
            exam_groups[exam_id] = {
                'exam': mark.exam,
                'subject_marks': {},
                'total_obtained': 0,
                'total_max': 0,
                'failed_any': False,
                'has_marks': False
            }
        
        subj_id = mark.subject.id if mark.subject else None
        is_pass = mark.marks_obtained >= mark.exam.passing_marks
        
        exam_groups[exam_id]['subject_marks'][subj_id] = {
            'obtained': mark.marks_obtained,
            'is_pass': is_pass
        }
        
        exam_groups[exam_id]['total_obtained'] += mark.marks_obtained
        exam_groups[exam_id]['total_max'] += mark.exam.max_marks
        if not is_pass:
            exam_groups[exam_id]['failed_any'] = True
        exam_groups[exam_id]['has_marks'] = True

    for eg in exam_groups.values():
        total_obtained = eg['total_obtained']
        total_max = eg['total_max']
        eg['percentage'] = (total_obtained / total_max * 100) if total_max > 0 else 0
        eg['is_pass'] = not eg['failed_any'] and eg['has_marks']
        
        # Pre-format ordered marks list for the template matrix
        row_marks = []
        if subjects.exists():
            for subject in subjects:
                m_info = eg['subject_marks'].get(subject.id)
                row_marks.append({
                    'subject_name': subject.name,
                    'obtained': m_info['obtained'] if m_info else None,
                    'is_pass': m_info['is_pass'] if m_info else False,
                    'has_mark': m_info is not None
                })
        else:
            m_info = eg['subject_marks'].get(None)
            row_marks.append({
                'subject_name': 'General',
                'obtained': m_info['obtained'] if m_info else None,
                'is_pass': m_info['is_pass'] if m_info else False,
                'has_mark': m_info is not None
            })
        eg['row_marks'] = row_marks

    detailed_mark_list = list(exam_groups.values())
    detailed_mark_list.sort(key=lambda x: x['exam'].date, reverse=True)

    # 6. JSON-serializable data for Chart.js (Chronological order)
    chart_list = list(exam_groups.values())
    chart_list.sort(key=lambda x: x['exam'].date)
    
    serializable_marks = []
    for item in chart_list:
        subj_marks = {}
        for sm_id, sm_info in item['subject_marks'].items():
            subj_marks[str(sm_id) if sm_id is not None else "None"] = float(sm_info['obtained'])
            
        serializable_marks.append({
            'exam_name': item['exam'].name,
            'exam_date': item['exam'].date.strftime('%Y-%m-%d'),
            'total_obtained': float(item['total_obtained']),
            'total_max': float(item['total_max']),
            'percentage': float(item['percentage']),
            'subject_marks': subj_marks
        })
        
    chart_subjects = [{'id': s.id, 'name': s.name} for s in subjects]

    # 7. Calendar Data
    attendance_records = Attendance.objects.filter(student=student)
    attendance_dict = {att.date.strftime('%Y-%m-%d'): att.status for att in attendance_records}
    
    working_days = WorkingDay.objects.all()
    working_days_dict = {wd.date.strftime('%Y-%m-%d'): wd.is_working_day for wd in working_days}

    return {
        'student': student,
        'attendance_percentage': attendance_percentage,
        'today_status': today_status,
        'attendance_json': json.dumps(attendance_dict),
        'working_days_json': json.dumps(working_days_dict),
        'chart_labels': labels,
        'chart_data': data,
        'subjects': subjects,
        'detailed_mark_list': detailed_mark_list,
        'chart_marks_json': serializable_marks,
        'chart_subjects_json': chart_subjects,
        'leaderboard': leaderboard_data,
        'upcoming_exams': upcoming_exams,
        'today_tasks': today_tasks,
    }

def parent_dashboard_view(request):
    student_id = request.session.get('logged_in_student')
    if not student_id:
        return redirect('parent_login')
        
    student = get_object_or_404(Student, id=student_id)
    context = get_student_dashboard_context(student)
    return render(request, 'tuition/parent_dashboard.html', context)

def download_progress_card_pdf_view(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    
    # Permission checks similar to dashboard
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role in ['teacher', 'admin']:
        if profile.centre and student.class_group.centre != profile.centre:
            messages.error(request, "Access denied.")
            return redirect('landing_page')
    elif not (request.user.is_superuser or request.user.is_staff):
        logged_in_id = request.session.get('logged_in_student')
        if not logged_in_id or int(logged_in_id) != student.id:
            messages.error(request, "Access denied.")
            return redirect('parent_login')
            
    context = get_student_dashboard_context(student)
    template = get_template('tuition/progress_card_pdf.html')
    html = template.render(context)
    
    result = BytesIO()
    if pisa:
        pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
        if not pdf.err:
            response = HttpResponse(result.getvalue(), content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{student.full_name}_Progress_Card.pdf"'
            return response
    
    messages.error(request, "Failed to generate PDF on the server.")
    return redirect(request.META.get('HTTP_REFERER', 'landing_page'))

@login_required(login_url='teacher_login')
def teacher_student_dashboard_view(request, student_id):
    profile = getattr(request.user, 'profile', None)
    if not (request.user.is_superuser or request.user.is_staff or (profile and profile.role in ['teacher', 'admin'])):
        messages.error(request, "Access denied.")
        return redirect('landing_page')
        
    student = get_object_or_404(Student, id=student_id)
    
    # Ensure a teacher can only view their own centre's students if restricted
    if profile and profile.role == 'teacher' and profile.centre:
        if student.class_group.centre != profile.centre:
            messages.error(request, "Access denied. Student not in your centre.")
            return redirect('teacher_dashboard')
            
    context = get_student_dashboard_context(student)
    context['is_teacher_viewing'] = True  # Add a flag to context if needed
    return render(request, 'tuition/parent_dashboard.html', context)

# --- Homework Completion Views ---

from django.contrib import messages
from django.utils import timezone

def parent_complete_task(request, task_id):
    """Allow a parent to mark a HomeTask as completed for their child."""
    student_id = request.session.get('logged_in_student')
    if not student_id:
        return redirect('parent_login')
    student = get_object_or_404(Student, id=student_id)
    task = get_object_or_404(HomeTask, id=task_id, class_group=student.class_group)
    # Create completion if not exists
    completion, created = HomeTaskCompletion.objects.get_or_create(student=student, task=task)
    if created:
        messages.success(request, "Home task marked as completed.")
    else:
        messages.info(request, "Home task was already marked as completed.")
    return redirect('parent_dashboard')

def parent_delete_completion(request, completion_id):
    """Allow a parent to delete their completion (editability)."""
    student_id = request.session.get('logged_in_student')
    if not student_id:
        return redirect('parent_login')
    student = get_object_or_404(Student, id=student_id)
    completion = get_object_or_404(HomeTaskCompletion, id=completion_id, student=student)
    completion.delete()
    messages.success(request, "Home task completion removed. You can re-mark it if needed.")
    return redirect('parent_dashboard')

def teacher_verify_completion(request, completion_id):
    """Allow a teacher to verify a student's task completion."""
    if not hasattr(request.user, 'profile') or request.user.profile.role != 'teacher':
        messages.error(request, "Only teachers can verify completions.")
        return redirect('teacher_dashboard')
    completion = get_object_or_404(HomeTaskCompletion, id=completion_id)
    completion.verified = True
    completion.verified_by = request.user
    completion.verified_at = timezone.now()
    completion.save()
    messages.success(request, "Completion verified.")
    # Redirect back to the task management page for the class
    class_id = completion.task.class_group.id
    return redirect('manage_tasks', class_id=class_id)



# --- Teacher / Admin Views ---

def teacher_login_view(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            if hasattr(user, 'profile') and user.profile.role == 'teacher':
                login(request, user)
                return redirect('teacher_dashboard')
            else:
                messages.error(request, 'Access Denied. Teacher account required.')
        else:
            messages.error(request, 'Invalid Username or Password.')
    return render(request, 'tuition/teacher_login.html')


def teacher_logout_view(request):
    logout(request)
    return redirect('landing_page')

def admin_login_view(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            if user.is_superuser or (hasattr(user, 'profile') and user.profile.role == 'admin'):
                login(request, user)
                return redirect('admin_dashboard')
            else:
                messages.error(request, 'Access Denied. Admin account required.')
        else:
            messages.error(request, 'Invalid Username or Password.')
    return render(request, 'tuition/admin_login.html')


def admin_logout_view(request):
    logout(request)
    return redirect('landing_page')

@login_required(login_url='teacher_login')
def teacher_dashboard_view(request):
    classes = ClassGroup.objects.all()
    profile = getattr(request.user, 'profile', None)
    
    if profile and profile.role == 'teacher':
        # Enforce that the teacher sees all classes in their designated centre
        if profile.centre:
            classes = ClassGroup.objects.filter(centre=profile.centre)
        else:
            classes = ClassGroup.objects.filter(teacher=request.user)
            
    return render(request, 'tuition/teacher_dashboard.html', {'classes': classes})

@login_required(login_url='teacher_login')
def mark_attendance_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
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

@login_required(login_url='teacher_login')
def monthly_attendance_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
        class_group = get_object_or_404(ClassGroup, id=class_id)
        
    students = class_group.students.all()
    
    import calendar
    from datetime import date
    
    # Get requested month and year, default to current
    month = int(request.GET.get('month', timezone.now().month))
    year = int(request.GET.get('year', timezone.now().year))
    
    # Get all configured days for this month (both working and non-working)
    working_days = WorkingDay.objects.filter(
        date__year=year, 
        date__month=month
    ).order_by('date')
    
    # Fetch existing attendance
    attendances = Attendance.objects.filter(
        student__in=students, 
        date__year=year, 
        date__month=month
    )
    
    # Organize attendance by student and date for easy lookup in template
    attendance_map = {}
    for att in attendances:
        if att.student_id not in attendance_map:
            attendance_map[att.student_id] = {}
        attendance_map[att.student_id][att.date] = att.status
        
    if request.method == 'POST':
        for student in students:
            for wd in working_days:
                if not wd.is_working_day or wd.date > timezone.now().date():
                    continue
                att_key = f"att_{student.id}_{wd.date.strftime('%Y-%m-%d')}"
                status_val = request.POST.get(att_key)
                if status_val in ['present', 'absent', 'late']:
                    Attendance.objects.update_or_create(
                        student=student,
                        date=wd.date,
                        defaults={'status': status_val, 'marked_by': request.user}
                    )
                elif status_val == 'unmarked':
                    Attendance.objects.filter(student=student, date=wd.date).delete()
        messages.success(request, f'Monthly attendance updated successfully for {calendar.month_name[month]} {year}.')
        return redirect('monthly_attendance', class_id=class_id)
        
    # Generate list of months for dropdown
    months = [{'value': i, 'name': calendar.month_name[i]} for i in range(1, 13)]
    
    context = {
        'class_group': class_group,
        'students': students,
        'working_days': working_days,
        'attendance_map': attendance_map,
        'current_month': month,
        'current_year': year,
        'months': months,
        'today': timezone.now().date()
    }
    return render(request, 'tuition/monthly_attendance.html', context)

@login_required(login_url='teacher_login')
def enter_marks_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
        class_group = get_object_or_404(ClassGroup, id=class_id)
        
    exams = class_group.exams.all()
    subjects = class_group.subjects.all()
    students = class_group.students.all()
    
    if request.method == 'POST':
        exam_id = request.POST.get('exam_id')
        subject_id = request.POST.get('subject_id')
        exam = get_object_or_404(Exam, id=exam_id)
        
        subject = None
        if not subject_id and exam.subject:
            subject = exam.subject
        elif subject_id:
            subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
            
        for student in students:
            # Handle duplicate inputs from mobile/desktop views by checking all submitted values
            marks_list = request.POST.getlist(f'marks_{student.id}')
            # Find the first non-empty mark, if any
            marks = next((m for m in marks_list if m.strip()), "")
            
            if marks:
                MarkEntry.objects.update_or_create(
                    student=student,
                    exam=exam,
                    subject=subject,
                    defaults={'marks_obtained': marks, 'entered_by': request.user}
                )
            else:
                # If marks is empty, delete any existing MarkEntry
                MarkEntry.objects.filter(student=student, exam=exam, subject=subject).delete()
        
        subject_name = f" ({subject.name})" if subject else ""
        messages.success(request, f'Marks entered for {exam.name}{subject_name}.')
        return redirect('teacher_dashboard')
        
    exam_id = request.GET.get('exam_id')
    subject_id = request.GET.get('subject_id')
    
    selected_exam = None
    selected_subject = None
    
    if exam_id:
        selected_exam = get_object_or_404(Exam, id=exam_id, class_group=class_group)
        if selected_exam.subject:
            selected_subject = selected_exam.subject
        elif subject_id:
            selected_subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
    elif subject_id:
        selected_subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
        
    show_marks = False
    if selected_exam:
        if not subjects.exists():
            show_marks = True
        elif selected_subject:
            show_marks = True
            
    if show_marks:
        marks = MarkEntry.objects.filter(exam=selected_exam, subject=selected_subject)
        marks_dict = {mark.student_id: mark.marks_obtained for mark in marks}
        for student in students:
            student.existing_mark = marks_dict.get(student.id, '')
            
    context = {
        'class_group': class_group,
        'exams': exams,
        'subjects': subjects,
        'students': students,
        'selected_exam': selected_exam,
        'selected_subject': selected_subject,
        'show_marks': show_marks
    }
    return render(request, 'tuition/enter_marks.html', context)

@login_required(login_url='teacher_login')
def view_marks_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
        class_group = get_object_or_404(ClassGroup, id=class_id)
        
    exams = class_group.exams.all()
    subjects = class_group.subjects.all()
    
    selected_exam = None
    selected_subject = None
    mark_entries = []
    
    exam_id = request.GET.get('exam_id')
    subject_id = request.GET.get('subject_id')
    
    if exam_id:
        selected_exam = get_object_or_404(Exam, id=exam_id, class_group=class_group)
        if selected_exam.subject:
            selected_subject = selected_exam.subject
        elif subject_id:
            selected_subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
    elif subject_id:
        selected_subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
        
    show_marks = False
    if selected_exam:
        if not subjects.exists():
            show_marks = True
        elif selected_subject:
            show_marks = True
            
    if show_marks:
        mark_entries = MarkEntry.objects.filter(exam=selected_exam, subject=selected_subject).order_by('-marks_obtained')
        
        # Calculate rank
        rank = 1
        for i, entry in enumerate(mark_entries):
            if i > 0 and entry.marks_obtained < mark_entries[i-1].marks_obtained:
                rank = i + 1
            entry.rank = rank

    context = {
        'class_group': class_group,
        'exams': exams,
        'subjects': subjects,
        'selected_exam': selected_exam,
        'selected_subject': selected_subject,
        'mark_entries': mark_entries,
        'show_marks': show_marks
    }
    return render(request, 'tuition/view_marks.html', context)


@login_required(login_url='teacher_login')
def manage_tasks_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
        class_group = get_object_or_404(ClassGroup, id=class_id)
        
    subjects = class_group.subjects.all()
    tasks = class_group.tasks.all().order_by('-date', '-created_at')
    
    for task in tasks:
        task.all_completions = task.completions.all().select_related('student', 'verified_by')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        subject_id = request.POST.get('subject_id')
        date = request.POST.get('date')
        
        if title and date:
            try:
                subject = None
                if subject_id:
                    subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
                
                HomeTask.objects.create(
                    title=title,
                    description=description,
                    class_group=class_group,
                    subject=subject,
                    date=date,
                    created_by=request.user
                )
                messages.success(request, 'Home Task created successfully.')
            except Exception as e:
                messages.error(request, f'Failed to create home task: {e}')
        else:
            messages.error(request, 'Title and Date are required fields.')
            
        return redirect('manage_tasks', class_id=class_id)
        
    return render(request, 'tuition/manage_tasks.html', {
        'class_group': class_group,
        'subjects': subjects,
        'tasks': tasks
    })


@login_required(login_url='teacher_login')
def delete_task_view(request, task_id):
    task = get_object_or_404(HomeTask, id=task_id)
    class_id = task.class_group.id
    
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        if task.class_group.centre != profile.centre:
            messages.error(request, 'You do not have permission to delete this task.')
            return redirect('teacher_dashboard')
            
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Home Task deleted successfully.')
        
    return redirect('manage_tasks', class_id=class_id)


@login_required(login_url='teacher_login')
def teacher_class_students_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
        class_group = get_object_or_404(ClassGroup, id=class_id)
        
    students = class_group.students.filter(is_active=True).order_by('first_name')
    return render(request, 'tuition/teacher_class_students.html', {
        'class_group': class_group,
        'students': students
    })

# --- Admin Portal Views ---

def is_admin(user):
    return user.is_superuser or user.is_staff or (hasattr(user, 'profile') and user.profile.role == 'admin')

def get_admin_centre(request):
    if request.user.is_authenticated and not request.user.is_superuser:
        profile = getattr(request.user, 'profile', None)
        if profile and profile.role == 'admin' and profile.centre:
            return profile.centre
            
    centre_id = request.session.get('active_centre_id')
    if centre_id:
        try:
            return Centre.objects.get(id=centre_id)
        except Centre.DoesNotExist:
            request.session['active_centre_id'] = None
    return None

@login_required(login_url='teacher_login')
@user_passes_test(is_admin, login_url='/teacher/')
def admin_dashboard_view(request):
    active_centre = get_admin_centre(request)
    
    student_qs = Student.objects.filter(is_active=True)
    teacher_qs = User.objects.filter(profile__role='teacher')
    
    today = timezone.now().date()
    attendance_qs = Attendance.objects.filter(date=today)
    
    if active_centre:
        student_qs = student_qs.filter(class_group__centre=active_centre)
        teacher_qs = teacher_qs.filter(profile__centre=active_centre)
        attendance_qs = attendance_qs.filter(student__class_group__centre=active_centre)
        
    total_students = student_qs.count()
    total_teachers = teacher_qs.count()
    total_present = attendance_qs.filter(status='present').count()
    total_absent = attendance_qs.filter(status='absent').count()
    
    context = {
        'total_students': total_students,
        'total_teachers': total_teachers,
        'total_present': total_present,
        'total_absent': total_absent,
    }
    return render(request, 'tuition/admin_dashboard.html', context)

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_classes_view(request):
    active_centre = get_admin_centre(request)
    if request.method == 'POST':
        name = request.POST.get('name')
        centre_id = request.POST.get('centre_id')
        
        if active_centre:
            target_centre = active_centre
        elif centre_id:
            target_centre = get_object_or_404(Centre, id=centre_id)
        else:
            target_centre = Centre.objects.first()
            
        if name:
            if not target_centre:
                messages.error(request, 'Please create a centre first.')
            elif ClassGroup.objects.filter(name=name, centre=target_centre).exists():
                messages.error(request, 'A class with this name already exists in this centre.')
            else:
                ClassGroup.objects.create(name=name, centre=target_centre)
                messages.success(request, f'Class created successfully for {target_centre.name}.')
        return redirect('admin_classes')
        
    if active_centre:
        classes = ClassGroup.objects.filter(centre=active_centre)
    else:
        classes = ClassGroup.objects.all()
    return render(request, 'tuition/admin_classes.html', {'classes': classes, 'active_centre': active_centre})

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_edit_class_view(request, class_id):
    class_group = get_object_or_404(ClassGroup, id=class_id)
    active_centre = get_admin_centre(request)
    
    if active_centre and class_group.centre != active_centre:
        messages.error(request, 'You do not have permission to edit this class.')
        return redirect('admin_classes')
        
    if request.method == 'POST':
        name = request.POST.get('name')
        centre_id = request.POST.get('centre_id')
        teacher_id = request.POST.get('teacher_id')
        description = request.POST.get('description', '')
        
        if name:
            if active_centre:
                target_centre = active_centre
            elif centre_id:
                target_centre = get_object_or_404(Centre, id=centre_id)
            else:
                target_centre = class_group.centre
                
            if ClassGroup.objects.filter(name=name, centre=target_centre).exclude(id=class_id).exists():
                messages.error(request, 'A class with this name already exists in this centre.')
            else:
                try:
                    class_group.name = name
                    class_group.centre = target_centre
                    class_group.description = description
                    if teacher_id:
                        class_group.teacher = get_object_or_404(User, id=teacher_id, profile__role='teacher')
                    else:
                        class_group.teacher = None
                    class_group.save()
                    messages.success(request, 'Class updated successfully.')
                    return redirect('admin_classes')
                except Exception as e:
                    messages.error(request, f'Failed to update class: {e}')
        else:
            messages.error(request, 'Class Name is required.')
            
    if active_centre:
        all_centres = [active_centre]
        teachers = User.objects.filter(profile__role='teacher', profile__centre=active_centre)
    else:
        all_centres = Centre.objects.all()
        teachers = User.objects.filter(profile__role='teacher')
        
    return render(request, 'tuition/admin_edit_class.html', {
        'class_group': class_group,
        'all_centres': all_centres,
        'teachers': teachers,
        'active_centre': active_centre
    })

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_delete_class_view(request, class_id):
    class_group = get_object_or_404(ClassGroup, id=class_id)
    active_centre = get_admin_centre(request)
    
    if active_centre and class_group.centre != active_centre:
        messages.error(request, 'You do not have permission to delete this class.')
        return redirect('admin_classes')
        
    if request.method == 'POST':
        class_group.delete()
        messages.success(request, 'Class deleted successfully.')
    return redirect('admin_classes')

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_subjects_view(request, class_id):
    class_group = get_object_or_404(ClassGroup, id=class_id)
    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            try:
                Subject.objects.create(name=name, class_group=class_group)
                messages.success(request, 'Subject added successfully.')
            except Exception as e:
                messages.error(request, f'Failed to add subject: {e}')
        else:
            messages.error(request, 'Please fill in all fields.')
        return redirect('admin_subjects', class_id=class_id)
        
    subjects = class_group.subjects.all()
    return render(request, 'tuition/admin_subjects.html', {'class_group': class_group, 'subjects': subjects})

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_edit_subject_view(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            try:
                subject.name = name
                subject.save()
                messages.success(request, 'Subject updated successfully.')
                return redirect('admin_subjects', class_id=subject.class_group.id)
            except Exception as e:
                messages.error(request, f'Failed to update subject: {e}')
        else:
            messages.error(request, 'Please fill in all fields.')
            
    return render(request, 'tuition/admin_edit_subject.html', {'subject': subject})

@login_required(login_url='admin_login')
@user_passes_test(is_admin, login_url='admin_login')
def admin_working_days_view(request):

    import calendar
    from datetime import date, timedelta
    
    month = int(request.GET.get('month', timezone.now().month))
    year = int(request.GET.get('year', timezone.now().year))
    
    if request.method == 'POST':
        # Admin is saving the working days config for the month
        month = int(request.POST.get('month', month))
        year = int(request.POST.get('year', year))
        
        # Get number of days in the month
        num_days = calendar.monthrange(year, month)[1]
        
        for day in range(1, num_days + 1):
            current_date = date(year, month, day)
            is_working_day = request.POST.get(f'day_{day}') == 'on'
            note = request.POST.get(f'note_{day}', '')
            
            WorkingDay.objects.update_or_create(
                date=current_date,
                defaults={
                    'is_working_day': is_working_day,
                    'note': note
                }
            )
        messages.success(request, f'Working days configured for {calendar.month_name[month]} {year}.')
        return redirect(f"{request.path}?month={month}&year={year}")

    num_days = calendar.monthrange(year, month)[1]
    days_data = []
    
    # Pre-fill data
    existing_wd = {wd.date.day: wd for wd in WorkingDay.objects.filter(date__year=year, date__month=month)}
    
    for day in range(1, num_days + 1):
        current_date = date(year, month, day)
        is_weekend = current_date.weekday() >= 5 # 5=Sat, 6=Sun
        
        if day in existing_wd:
            is_working = existing_wd[day].is_working_day
            note = existing_wd[day].note
        else:
            # Default logic: all days are working days in a tuition centre
            is_working = True
            note = ''
            
        days_data.append({
            'day': day,
            'date': current_date,
            'day_name': current_date.strftime('%A'),
            'is_weekend': is_weekend,
            'is_working': is_working,
            'note': note
        })

    months = [{'value': i, 'name': calendar.month_name[i]} for i in range(1, 13)]

    context = {
        'days_data': days_data,
        'current_month': month,
        'current_year': year,
        'months': months
    }
    return render(request, 'tuition/admin_working_days.html', context)

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_delete_subject_view(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    class_id = subject.class_group.id
    if request.method == 'POST':
        subject.delete()
        messages.success(request, 'Subject deleted successfully.')
    return redirect('admin_subjects', class_id=class_id)

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_students_view(request):
    active_centre = get_admin_centre(request)
    if request.method == 'POST':
        student_id = request.POST.get('student_id', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        class_id = request.POST.get('class_id')
        pin = request.POST.get('pin', '').strip()
        
        if all([student_id, first_name, last_name, class_id]):
            if not pin:
                pin = generate_pin()
            class_group = get_object_or_404(ClassGroup, id=class_id)
            Student.objects.create(
                student_id=student_id,
                first_name=first_name,
                last_name=last_name,
                class_group=class_group,
                pin=pin
            )
            messages.success(request, 'Student added successfully.')
        else:
            messages.error(request, 'Please fill in all required fields.')
        return redirect('admin_students')

    if active_centre:
        classes = ClassGroup.objects.filter(centre=active_centre).prefetch_related('students')
    else:
        classes = ClassGroup.objects.all().prefetch_related('students')

    # Build class-wise student groups
    class_groups = []
    for cls in classes:
        class_students = cls.students.all().order_by('student_id')
        class_groups.append({'class': cls, 'students': class_students})

    all_classes = classes
    return render(request, 'tuition/admin_students.html', {
        'class_groups': class_groups,
        'classes': all_classes,
        'active_centre': active_centre,
    })


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_student_detail_view(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    active_centre = get_admin_centre(request)

    if active_centre and student.class_group.centre != active_centre:
        messages.error(request, 'You do not have permission to view this student.')
        return redirect('admin_students')

    return render(request, 'tuition/admin_student_detail.html', {
        'student': student,
        'active_centre': active_centre,
    })


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_edit_student_view(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    active_centre = get_admin_centre(request)
    
    if active_centre and student.class_group.centre != active_centre:
        messages.error(request, 'You do not have permission to edit this student.')
        return redirect('admin_students')
        
    if request.method == 'POST':
        student_id_val = request.POST.get('student_id', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        class_id = request.POST.get('class_id')
        pin = request.POST.get('pin', '').strip()
        
        if all([student_id_val, first_name, last_name, class_id]):
            class_group = get_object_or_404(ClassGroup, id=class_id)
            if active_centre and class_group.centre != active_centre:
                messages.error(request, 'Invalid class group selection.')
            else:
                student.student_id = student_id_val
                student.first_name = first_name
                student.last_name = last_name
                student.class_group = class_group
                if pin:
                    student.pin = pin
                else:
                    if not student.pin:
                        student.pin = generate_pin()
                try:
                    student.save()
                    messages.success(request, 'Student updated successfully.')
                    return redirect('admin_students')
                except Exception as e:
                    messages.error(request, f'Failed to update student: {e}')
        else:
            messages.error(request, 'Please fill in all required fields.')
            
    if active_centre:
        classes = ClassGroup.objects.filter(centre=active_centre)
    else:
        classes = ClassGroup.objects.all()
        
    return render(request, 'tuition/admin_edit_student.html', {
        'student': student,
        'classes': classes,
        'active_centre': active_centre
    })


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_delete_student_view(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    active_centre = get_admin_centre(request)
    
    if active_centre and student.class_group.centre != active_centre:
        messages.error(request, 'You do not have permission to delete this student.')
        return redirect('admin_students')
        
    if request.method == 'POST':
        student.delete()
        messages.success(request, 'Student deleted successfully.')
        
    return redirect('admin_students')


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_bulk_delete_students_view(request):
    active_centre = get_admin_centre(request)
    if request.method == 'POST':
        student_ids = request.POST.getlist('selected_students')
        if student_ids:
            qs = Student.objects.filter(id__in=student_ids)
            if active_centre:
                qs = qs.filter(class_group__centre=active_centre)
            
            deleted_count = qs.count()
            if deleted_count > 0:
                qs.delete()
                messages.success(request, f'Successfully deleted {deleted_count} student(s).')
            else:
                messages.error(request, 'No valid students selected for deletion.')
        else:
            messages.warning(request, 'No students selected.')
            
    return redirect('admin_students')

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_exams_view(request):
    active_centre = get_admin_centre(request)
    if request.method == 'POST':
        name = request.POST.get('name')
        class_id = request.POST.get('class_id')
        subject_id = request.POST.get('subject_id')
        max_marks = request.POST.get('max_marks')
        passing_marks = request.POST.get('passing_marks')
        date = request.POST.get('date')
        
        if all([name, class_id, max_marks, passing_marks, date]):
            try:
                class_group = get_object_or_404(ClassGroup, id=class_id)
                subject = None
                if subject_id:
                    subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
                Exam.objects.create(
                    name=name,
                    class_group=class_group,
                    subject=subject,
                    date=date,
                    max_marks=max_marks,
                    passing_marks=passing_marks,
                    created_by=request.user
                )
                messages.success(request, 'Exam created successfully.')
            except Exception as e:
                messages.error(request, f'Failed to create exam: {e}')
        else:
            messages.error(request, 'Please fill in all fields.')
        return redirect('admin_exams')
        
    if active_centre:
        exams = Exam.objects.filter(class_group__centre=active_centre)
        classes = ClassGroup.objects.filter(centre=active_centre)
        subjects = Subject.objects.filter(class_group__centre=active_centre)
    else:
        exams = Exam.objects.all()
        classes = ClassGroup.objects.all()
        subjects = Subject.objects.all()
    return render(request, 'tuition/admin_exams.html', {
        'exams': exams,
        'classes': classes,
        'subjects': subjects,
        'active_centre': active_centre
    })

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_edit_exam_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    active_centre = get_admin_centre(request)
    
    if request.method == 'POST':
        name = request.POST.get('name')
        class_id = request.POST.get('class_id')
        subject_id = request.POST.get('subject_id')
        max_marks = request.POST.get('max_marks')
        passing_marks = request.POST.get('passing_marks')
        date = request.POST.get('date')
        
        if all([name, class_id, max_marks, passing_marks, date]):
            try:
                class_group = get_object_or_404(ClassGroup, id=class_id)
                subject = None
                if subject_id:
                    subject = get_object_or_404(Subject, id=subject_id, class_group=class_group)
                exam.name = name
                exam.class_group = class_group
                exam.subject = subject
                exam.max_marks = max_marks
                exam.passing_marks = passing_marks
                exam.date = date
                exam.save()
                messages.success(request, 'Exam updated successfully.')
                return redirect('admin_exams')
            except Exception as e:
                messages.error(request, f'Failed to update exam: {e}')
        else:
            messages.error(request, 'Please fill in all fields.')
            
    if active_centre:
        classes = ClassGroup.objects.filter(centre=active_centre)
        subjects = Subject.objects.filter(class_group__centre=active_centre)
    else:
        classes = ClassGroup.objects.all()
        subjects = Subject.objects.all()
        
    return render(request, 'tuition/admin_edit_exam.html', {
        'exam': exam,
        'classes': classes,
        'subjects': subjects
    })

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_delete_exam_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if request.method == 'POST':
        exam.delete()
        messages.success(request, 'Exam deleted successfully.')
    return redirect('admin_exams')

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_teachers_view(request):
    active_centre = get_admin_centre(request)
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        password = request.POST.get('password')
        centre_id = request.POST.get('centre_id')
        
        if active_centre:
            target_centre = active_centre
        elif centre_id:
            target_centre = get_object_or_404(Centre, id=centre_id)
        else:
            target_centre = Centre.objects.first()
            
        if all([username, password]):
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    password=password
                )
                UserProfile.objects.create(user=user, role='teacher', centre=target_centre)
                messages.success(request, 'Teacher added successfully.')
            else:
                messages.error(request, 'Username already exists.')
        return redirect('admin_teachers')
        
    if active_centre:
        teachers = User.objects.filter(profile__role='teacher', profile__centre=active_centre)
    else:
        teachers = User.objects.filter(profile__role='teacher')
    return render(request, 'tuition/admin_teachers.html', {'teachers': teachers, 'active_centre': active_centre})

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_bulk_teachers_view(request):
    active_centre = get_admin_centre(request)
    target_centre = active_centre or Centre.objects.first()
    
    if request.method == 'POST' and request.FILES.get('file'):
        csv_file = request.FILES['file']
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a valid CSV file.')
            return redirect('admin_teachers')
            
        try:
            file_data = csv_file.read().decode('utf-8').splitlines()
            reader = csv.reader(file_data)
            next(reader, None) # Skip header
            
            count = 0
            for row in reader:
                if len(row) >= 5:
                    username, email, first_name, last_name, password = [item.strip() for item in row[:5]]
                    if username and password and not User.objects.filter(username=username).exists():
                        user = User.objects.create_user(
                            username=username,
                            email=email,
                            first_name=first_name,
                            last_name=last_name,
                            password=password
                        )
                        UserProfile.objects.create(user=user, role='teacher', centre=target_centre)
                        count += 1
            messages.success(request, f'Successfully imported {count} teachers to {target_centre.name if target_centre else "default branch"}.')
        except Exception as e:
            messages.error(request, f'Error processing file: {str(e)}')
            
    return redirect('admin_teachers')

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_edit_teacher_view(request, teacher_id):
    teacher = get_object_or_404(User, id=teacher_id, profile__role='teacher')
    
    # Check permissions
    active_centre = get_admin_centre(request)
    if active_centre and teacher.profile.centre != active_centre:
        messages.error(request, 'You do not have permission to edit this teacher.')
        return redirect('admin_teachers')
        
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        centre_id = request.POST.get('centre_id')
        
        if first_name:
            teacher.first_name = first_name
            teacher.last_name = last_name
            teacher.email = email
            if password:
                teacher.set_password(password)
            teacher.save()
            
            # Update centre if not active_centre locked
            if not active_centre and centre_id:
                try:
                    c = Centre.objects.get(id=centre_id)
                    teacher.profile.centre = c
                    teacher.profile.save()
                except Centre.DoesNotExist:
                    pass
                    
            messages.success(request, 'Teacher updated successfully.')
            return redirect('admin_teachers')
        else:
            messages.error(request, 'First name is required.')
            
    all_centres = Centre.objects.all()
    return render(request, 'tuition/admin_edit_teacher.html', {
        'teacher': teacher,
        'all_centres': all_centres,
        'active_centre': active_centre
    })

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_delete_teacher_view(request, teacher_id):
    teacher = get_object_or_404(User, id=teacher_id, profile__role='teacher')
    
    # Check permissions
    active_centre = get_admin_centre(request)
    if active_centre and teacher.profile.centre != active_centre:
        messages.error(request, 'You do not have permission to delete this teacher.')
        return redirect('admin_teachers')
        
    if request.method == 'POST':
        teacher.delete()
        messages.success(request, 'Teacher deleted successfully.')
        
    return redirect('admin_teachers')

@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_bulk_students_view(request):
    active_centre = get_admin_centre(request)
    target_centre = active_centre or Centre.objects.first()
    
    if request.method == 'POST' and request.FILES.get('file'):
        csv_file = request.FILES['file']
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a valid CSV file.')
            return redirect('admin_students')
            
        try:
            file_data = csv_file.read().decode('utf-8').splitlines()
            reader = csv.reader(file_data)
            next(reader, None) # Skip header
            
            count = 0
            for row in reader:
                if len(row) >= 4:
                    student_id = row[0].strip()
                    first_name = row[1].strip()
                    last_name = row[2].strip()
                    class_name = row[3].strip()
                    pin = row[4].strip() if len(row) > 4 and row[4].strip() else generate_pin()
                    
                    if student_id and class_name and not Student.objects.filter(student_id=student_id).exists():
                        class_group, _ = ClassGroup.objects.get_or_create(name=class_name, centre=target_centre)
                        Student.objects.create(
                            student_id=student_id,
                            first_name=first_name,
                            last_name=last_name,
                            class_group=class_group,
                            pin=pin
                        )
                        count += 1
            messages.success(request, f'Successfully imported {count} students to {target_centre.name if target_centre else "default branch"}.')
        except Exception as e:
            messages.error(request, f'Error processing file: {str(e)}')
            
    return redirect('admin_students')


# Centre Management Views
@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_switch_centre_view(request, centre_id):
    if centre_id == 0:
        request.session['active_centre_id'] = None
        messages.success(request, 'Switched to All Centres.')
    else:
        centre = get_object_or_404(Centre, id=centre_id)
        request.session['active_centre_id'] = centre.id
        messages.success(request, f'Switched to {centre.name}.')
    
    referrer = request.META.get('HTTP_REFERER')
    if referrer:
        return redirect(referrer)
    return redirect('admin_dashboard')


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_centres_view(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        address = request.POST.get('address', '').strip()
        contact_number = request.POST.get('contact_number', '').strip()
        
        if name and code:
            if Centre.objects.filter(name__iexact=name).exists():
                messages.error(request, 'A centre with this name already exists.')
            elif Centre.objects.filter(code__iexact=code).exists():
                messages.error(request, 'A centre with this code already exists.')
            else:
                Centre.objects.create(name=name, code=code, address=address, contact_number=contact_number)
                messages.success(request, 'Centre added successfully.')
        else:
            messages.error(request, 'Name and code are required.')
        return redirect('admin_centres')
        
    centres = Centre.objects.all().order_by('name')
    for c in centres:
        c.student_count = Student.objects.filter(class_group__centre=c).count()
        c.staff_count = UserProfile.objects.filter(centre=c).count()
        
    return render(request, 'tuition/admin_centres.html', {'centres': centres})


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_edit_centre_view(request, centre_id):
    centre = get_object_or_404(Centre, id=centre_id)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        address = request.POST.get('address', '').strip()
        contact_number = request.POST.get('contact_number', '').strip()
        
        if name and code:
            if Centre.objects.filter(name__iexact=name).exclude(id=centre.id).exists():
                messages.error(request, 'A centre with this name already exists.')
            elif Centre.objects.filter(code__iexact=code).exclude(id=centre.id).exists():
                messages.error(request, 'A centre with this code already exists.')
            else:
                centre.name = name
                centre.code = code
                centre.address = address
                centre.contact_number = contact_number
                centre.save()
                messages.success(request, 'Centre updated successfully.')
                return redirect('admin_centres')
        else:
            messages.error(request, 'Name and code are required.')
            
    return render(request, 'tuition/admin_edit_centre.html', {'centre': centre})


@login_required(login_url='admin_login')
@user_passes_test(is_admin)
def admin_delete_centre_view(request, centre_id):
    centre = get_object_or_404(Centre, id=centre_id)
    if request.method == 'POST':
        centre.delete()
        messages.success(request, 'Centre deleted successfully.')
    return redirect('admin_centres')
