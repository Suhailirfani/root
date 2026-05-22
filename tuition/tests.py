from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from tuition.models import Centre, UserProfile, ClassGroup, Student, Exam, Subject, MarkEntry, Attendance, HomeTask

class MultiCentreTests(TestCase):
    def setUp(self):
        # 1. Create Centres
        self.town_branch = Centre.objects.create(name="Town Branch", code="TB", contact_number="123456", address="Town Road")
        self.city_branch = Centre.objects.create(name="City Branch", code="CB", contact_number="789012", address="City Square")
        
        # 2. Create Users
        self.admin_user = User.objects.create_superuser(username="admin", email="admin@test.com", password="adminpassword")
        self.admin_profile = UserProfile.objects.create(user=self.admin_user, role="admin", centre=None) # Global admin
        
        self.teacher_town = User.objects.create_user(username="teachertown", password="teacherpassword")
        self.teacher_town_profile = UserProfile.objects.create(user=self.teacher_town, role="teacher", centre=self.town_branch)
        
        self.teacher_city = User.objects.create_user(username="teachercity", password="teacherpassword")
        self.teacher_city_profile = UserProfile.objects.create(user=self.teacher_city, role="teacher", centre=self.city_branch)
        
        # 3. Create ClassGroups
        self.class_town = ClassGroup.objects.create(name="Grade 10", centre=self.town_branch, teacher=self.teacher_town)
        self.class_town_2 = ClassGroup.objects.create(name="Grade 11", centre=self.town_branch, teacher=None)
        self.class_city = ClassGroup.objects.create(name="Grade 10", centre=self.city_branch, teacher=self.teacher_city)
        
        # 4. Create Students
        self.student_town = Student.objects.create(student_id="STU001", first_name="John", last_name="Doe", class_group=self.class_town, pin="1234")
        self.student_city = Student.objects.create(student_id="STU002", first_name="Jane", last_name="Smith", class_group=self.class_city, pin="5678")
        
        # Initialize clients
        self.admin_client = Client()
        self.admin_client.login(username="admin", password="adminpassword")
        
        self.teacher_town_client = Client()
        self.teacher_town_client.login(username="teachertown", password="teacherpassword")
 
    def test_centre_crud(self):
        # View Centres
        response = self.admin_client.get(reverse('admin_centres'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Town Branch")
        self.assertContains(response, "City Branch")
        
        # Create Centre
        response = self.admin_client.post(reverse('admin_centres'), {
            'name': 'New Branch',
            'code': 'NB',
            'contact_number': '555555',
            'address': 'New Street'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Centre.objects.filter(name="New Branch").exists())
        
        # Edit Centre
        new_centre = Centre.objects.get(name="New Branch")
        response = self.admin_client.post(reverse('admin_edit_centre', args=[new_centre.id]), {
            'name': 'Updated Branch',
            'code': 'UB',
            'contact_number': '666666',
            'address': 'Updated Street'
        })
        self.assertEqual(response.status_code, 302)
        new_centre.refresh_from_db()
        self.assertEqual(new_centre.name, "Updated Branch")
        
        # Delete Centre
        response = self.admin_client.post(reverse('admin_delete_centre', args=[new_centre.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Centre.objects.filter(name="Updated Branch").exists())

    def test_centre_switching_and_filtering(self):
        # 1. Default (All Centres)
        response = self.admin_client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Should show total of both centres
        self.assertEqual(response.context['total_students'], 2)
        
        # 2. Switch to Town Branch
        response = self.admin_client.get(reverse('admin_switch_centre', args=[self.town_branch.id]))
        self.assertEqual(response.status_code, 302)
        
        # Check Dashboard shows only Town branch students
        response = self.admin_client.get(reverse('admin_dashboard'))
        self.assertEqual(response.context['total_students'], 1)
        
        # Check Classes view shows only Town branch classes
        response = self.admin_client.get(reverse('admin_classes'))
        self.assertEqual(len(response.context['classes']), 2)
        for class_group in response.context['classes']:
            self.assertEqual(class_group.centre, self.town_branch)
        
        # 3. Switch to City Branch
        self.admin_client.get(reverse('admin_switch_centre', args=[self.city_branch.id]))
        response = self.admin_client.get(reverse('admin_dashboard'))
        self.assertEqual(response.context['total_students'], 1)

    def test_teacher_isolation(self):
        # Teacher Town login should see all classes in Town branch (now 2 classes)
        response = self.teacher_town_client.get(reverse('teacher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['classes']), 2)
        for class_group in response.context['classes']:
            self.assertEqual(class_group.centre, self.town_branch)
        
        # Trying to access City Branch class attendance should return 404 (isolation!)
        response = self.teacher_town_client.get(reverse('mark_attendance', args=[self.class_city.id]))
        self.assertEqual(response.status_code, 404)
        
        # Accessing own Town class attendance should succeed
        response = self.teacher_town_client.get(reverse('mark_attendance', args=[self.class_town.id]))
        self.assertEqual(response.status_code, 200)

    def test_student_crud(self):
        # 1. View students list
        response = self.admin_client.get(reverse('admin_students'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Doe")
        self.assertContains(response, "Jane Smith")
        
        # 2. Add Student with PIN
        response = self.admin_client.post(reverse('admin_students'), {
            'student_id': 'STU003',
            'first_name': 'Alice',
            'last_name': 'Brown',
            'class_id': self.class_town.id,
            'pin': '9999'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.filter(student_id="STU003", pin="9999").exists())
        
        # 3. Add Student with empty PIN (should auto-generate)
        response = self.admin_client.post(reverse('admin_students'), {
            'student_id': 'STU004',
            'first_name': 'Bob',
            'last_name': 'White',
            'class_id': self.class_town.id,
            'pin': ''
        })
        self.assertEqual(response.status_code, 302)
        created_student = Student.objects.get(student_id="STU004")
        self.assertEqual(len(created_student.pin), 4)
        self.assertTrue(created_student.pin.isdigit())
        
        # 4. Edit Student
        response = self.admin_client.post(reverse('admin_edit_student', args=[created_student.id]), {
            'student_id': 'STU004-UPD',
            'first_name': 'Bobby',
            'last_name': 'White',
            'class_id': self.class_town_2.id,
            'pin': '8888'
        })
        self.assertEqual(response.status_code, 302)
        created_student.refresh_from_db()
        self.assertEqual(created_student.student_id, "STU004-UPD")
        self.assertEqual(created_student.first_name, "Bobby")
        self.assertEqual(created_student.class_group, self.class_town_2)
        self.assertEqual(created_student.pin, "8888")
        
        # 5. Delete Student
        response = self.admin_client.post(reverse('admin_delete_student', args=[created_student.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Student.objects.filter(id=created_student.id).exists())

    def test_student_bulk_delete(self):
        student3 = Student.objects.create(student_id="STU003", first_name="A", last_name="B", class_group=self.class_town, pin="1111")
        student4 = Student.objects.create(student_id="STU004", first_name="C", last_name="D", class_group=self.class_town, pin="2222")
        
        # Bulk delete student3 and student4
        response = self.admin_client.post(reverse('admin_bulk_delete_students'), {
            'selected_students': [student3.id, student4.id]
        })
        self.assertEqual(response.status_code, 302)
        
        self.assertFalse(Student.objects.filter(id=student3.id).exists())
        self.assertFalse(Student.objects.filter(id=student4.id).exists())
        # Make sure STU001 is not deleted
        self.assertTrue(Student.objects.filter(id=self.student_town.id).exists())


class SubjectSelectionTests(TestCase):
    def setUp(self):
        # Setup basic data
        self.centre = Centre.objects.create(name="Main Branch", code="MB")
        
        self.admin = User.objects.create_superuser(username="admin", password="adminpassword")
        self.admin_profile = UserProfile.objects.create(user=self.admin, role="admin", centre=self.centre)
        
        self.teacher = User.objects.create_user(username="teacher", password="teacherpassword")
        self.teacher_profile = UserProfile.objects.create(user=self.teacher, role="teacher", centre=self.centre)
        
        self.class_group = ClassGroup.objects.create(name="Class 10", centre=self.centre, teacher=self.teacher)
        self.subject = Subject.objects.create(name="Physics", class_group=self.class_group)
        self.student = Student.objects.create(student_id="STU101", first_name="Alice", last_name="Smith", class_group=self.class_group, pin="1234")
        
        self.admin_client = Client()
        self.admin_client.login(username="admin", password="adminpassword")
        
        self.teacher_client = Client()
        self.teacher_client.login(username="teacher", password="teacherpassword")

    def test_create_exam_with_subject(self):
        # Create exam with a subject
        response = self.admin_client.post(reverse('admin_exams'), {
            'name': 'Physics Midterm',
            'class_id': self.class_group.id,
            'subject_id': self.subject.id,
            'max_marks': '100',
            'passing_marks': '40',
            'date': '2026-05-20'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify exam is created and linked to Physics subject
        exam = Exam.objects.get(name="Physics Midterm")
        self.assertEqual(exam.subject, self.subject)
        self.assertEqual(exam.max_marks, 100)

    def test_edit_exam_subject(self):
        exam = Exam.objects.create(
            name="General Test",
            class_group=self.class_group,
            max_marks=100,
            passing_marks=40,
            date="2026-05-20"
        )
        self.assertIsNone(exam.subject)
        
        # Link it to Physics
        response = self.admin_client.post(reverse('admin_edit_exam', args=[exam.id]), {
            'name': 'Physics Test Updated',
            'class_id': self.class_group.id,
            'subject_id': self.subject.id,
            'max_marks': '100',
            'passing_marks': '40',
            'date': '2026-05-20'
        })
        self.assertEqual(response.status_code, 302)
        
        exam.refresh_from_db()
        self.assertEqual(exam.name, "Physics Test Updated")
        self.assertEqual(exam.subject, self.subject)

    def test_teacher_auto_selects_subject_in_enter_marks(self):
        exam = Exam.objects.create(
            name="Physics Final",
            class_group=self.class_group,
            subject=self.subject,
            max_marks=100,
            passing_marks=40,
            date="2026-05-20"
        )
        
        # Accessing enter_marks view without specifying subject_id in GET
        # The backend should automatically pre-select Physics because the exam has it linked
        response = self.teacher_client.get(reverse('enter_marks', args=[self.class_group.id]), {'exam_id': exam.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_subject'], self.subject)
        self.assertTrue(response.context['show_marks'])
        
        # Post marks for this exam and verify they save to the Physics subject
        response = self.teacher_client.post(reverse('enter_marks', args=[self.class_group.id]), {
            'exam_id': exam.id,
            # 'subject_id' is omitted, which tests if view automatically resolves it
            f'marks_{self.student.id}': '85'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify mark entry is correctly created with the correct subject
        mark = MarkEntry.objects.get(student=self.student, exam=exam)
        self.assertEqual(mark.subject, self.subject)
        self.assertEqual(mark.marks_obtained, 85)

    def test_today_attendance_status_on_parent_dashboard(self):
        # Establish parent dashboard session
        session = self.client.session
        session['logged_in_student'] = self.student.id
        session.save()
        
        # 1. No attendance today
        response = self.client.get(reverse('parent_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['today_status'])
        self.assertContains(response, "Not Marked")
        
        # 2. Mark student as present today
        today = timezone.now().date()
        Attendance.objects.create(student=self.student, date=today, status='present')
        
        response = self.client.get(reverse('parent_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['today_status'], 'present')
        self.assertContains(response, "Present")


class HomeTaskTests(TestCase):
    def setUp(self):
        # Setup test data
        self.centre = Centre.objects.create(name="Main Branch", code="MB")
        
        self.teacher = User.objects.create_user(username="teacher", password="teacherpassword")
        self.teacher_profile = UserProfile.objects.create(user=self.teacher, role="teacher", centre=self.centre)
        
        self.class_group = ClassGroup.objects.create(name="Class 10", centre=self.centre, teacher=self.teacher)
        self.subject = Subject.objects.create(name="Physics", class_group=self.class_group)
        self.student = Student.objects.create(student_id="STU101", first_name="Alice", last_name="Smith", class_group=self.class_group, pin="1234")
        
        self.teacher_client = Client()
        self.teacher_client.login(username="teacher", password="teacherpassword")
        
        # Parent Client setup
        self.parent_client = Client()
        session = self.parent_client.session
        session['logged_in_student'] = self.student.id
        session.save()

    def test_teacher_crud_home_task(self):
        # 1. View manage tasks page
        response = self.teacher_client.get(reverse('manage_tasks', args=[self.class_group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage Home Tasks")
        
        # 2. Add Home Task
        today = timezone.now().date().strftime('%Y-%m-%d')
        response = self.teacher_client.post(reverse('manage_tasks', args=[self.class_group.id]), {
            'title': 'Solve Homework 1',
            'description': 'Solve problems 1-10 on page 42.',
            'subject_id': self.subject.id,
            'date': today
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify it exists in DB
        task = HomeTask.objects.get(title="Solve Homework 1")
        self.assertEqual(task.description, "Solve problems 1-10 on page 42.")
        self.assertEqual(task.subject, self.subject)
        
        # 3. Delete Home Task
        response = self.teacher_client.post(reverse('delete_task', args=[task.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HomeTask.objects.filter(title="Solve Homework 1").exists())

    def test_parent_dashboard_renders_tasks_in_malayalam(self):
        # 1. Parent dashboard shows empty state in Malayalam when no tasks today
        response = self.parent_client.get(reverse('parent_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ഇന്നത്തേക്ക് ഗൃഹപാഠങ്ങൾ ഒന്നും നൽകിയിട്ടില്ല.") # Empty state
        
        # 2. Create home task for today
        today = timezone.now().date()
        HomeTask.objects.create(
            title="Read Chapter 5",
            description="Read pages 50-60 of textbook.",
            class_group=self.class_group,
            subject=self.subject,
            date=today,
            created_by=self.teacher
        )
        
        # 3. View parent dashboard, verify task rendered in Malayalam
        response = self.parent_client.get(reverse('parent_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ഇന്നത്തെ ഗൃഹപാഠം")
        self.assertContains(response, "Read Chapter 5")
        self.assertContains(response, "Read pages 50-60 of textbook.")
        self.assertContains(response, "വിഷയം: Physics")

