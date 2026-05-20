from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum, F, Window
from django.db.models.functions import Rank
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from .models import Student, Attendance, Exam, MarkEntry, ClassGroup, UserProfile, Subject, Centre, HomeTask, WorkingDay
from django.contrib.auth.models import User
import csv
import random
import string

def generate_pin(length=4):
    return ''.join(random.choices(string.digits, k=length))

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
    # Total working days = count of WorkingDay records since student joined up to today
    # Fallback to old logic if Admin hasn't configured any WorkingDays yet.
    if WorkingDay.objects.exists():
        total_days = WorkingDay.objects.filter(date__gte=student.created_at.date(), date__lte=timezone.now().date(), is_working_day=True).count()
    else:
        total_days = Attendance.objects.filter(student=student).count()
        
    present_days = Attendance.objects.filter(student=student, status='present').count()
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

    context = {
        'student': student,
        'attendance_percentage': attendance_percentage,
        'today_status': today_status,
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
    profile = getattr(request.user, 'profile', None)
    
    if profile and profile.role == 'teacher':
        # Enforce that the teacher sees all classes in their designated centre
        if profile.centre:
            classes = ClassGroup.objects.filter(centre=profile.centre)
        else:
            classes = ClassGroup.objects.filter(teacher=request.user)
            
    return render(request, 'tuition/teacher_dashboard.html', {'classes': classes})

@login_required
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

@login_required
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
                # the checkbox name will be something like att_studentId_date
                att_key = f"att_{student.id}_{wd.date.strftime('%Y-%m-%d')}"
                if att_key in request.POST:
                    Attendance.objects.update_or_create(
                        student=student,
                        date=wd.date,
                        defaults={'status': 'present', 'marked_by': request.user}
                    )
                else:
                    # If not checked, we mark as absent (only if we are updating or if it doesn't exist)
                    Attendance.objects.update_or_create(
                        student=student,
                        date=wd.date,
                        defaults={'status': 'absent', 'marked_by': request.user}
                    )
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

@login_required
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

@login_required
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


@login_required
def manage_tasks_view(request, class_id):
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role == 'teacher' and profile.centre:
        class_group = get_object_or_404(ClassGroup, id=class_id, centre=profile.centre)
    else:
        class_group = get_object_or_404(ClassGroup, id=class_id)
        
    subjects = class_group.subjects.all()
    tasks = class_group.tasks.all().order_by('-date', '-created_at')
    
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


@login_required
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


# --- Admin Portal Views ---

def is_admin(user):
    return user.is_superuser or (hasattr(user, 'profile') and user.profile.role == 'admin')

def get_admin_centre(request):
    centre_id = request.session.get('active_centre_id')
    if centre_id:
        try:
            return Centre.objects.get(id=centre_id)
        except Centre.DoesNotExist:
            request.session['active_centre_id'] = None
    return None

@login_required
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

@login_required
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

@login_required
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

@login_required
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

@login_required
def admin_working_days_view(request):
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'admin':
        return redirect('parent_login')

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

@login_required
@user_passes_test(is_admin)
def admin_delete_subject_view(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    class_id = subject.class_group.id
    if request.method == 'POST':
        subject.delete()
        messages.success(request, 'Subject deleted successfully.')
    return redirect('admin_subjects', class_id=class_id)

@login_required
@user_passes_test(is_admin)
def admin_students_view(request):
    active_centre = get_admin_centre(request)
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
        
    if active_centre:
        students = Student.objects.filter(class_group__centre=active_centre)
        classes = ClassGroup.objects.filter(centre=active_centre)
    else:
        students = Student.objects.all()
        classes = ClassGroup.objects.all()
    return render(request, 'tuition/admin_students.html', {'students': students, 'classes': classes, 'active_centre': active_centre})

@login_required
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

@login_required
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

@login_required
@user_passes_test(is_admin)
def admin_delete_exam_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if request.method == 'POST':
        exam.delete()
        messages.success(request, 'Exam deleted successfully.')
    return redirect('admin_exams')

@login_required
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

@login_required
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

@login_required
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

@login_required
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

@login_required
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
@login_required
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


@login_required
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


@login_required
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


@login_required
@user_passes_test(is_admin)
def admin_delete_centre_view(request, centre_id):
    centre = get_object_or_404(Centre, id=centre_id)
    if request.method == 'POST':
        centre.delete()
        messages.success(request, 'Centre deleted successfully.')
    return redirect('admin_centres')
