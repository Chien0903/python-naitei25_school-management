from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.db import transaction

from teachers.models import Teacher
from .models import Assign, ExamSession, Marks, AssignTime, AttendanceClass
from students.models import Attendance, StudentSubject

from utils.date_utils import determine_semester, determine_academic_year_start
from datetime import datetime, timedelta, date
import math
import re

from utils.constant import (
    DAYS_OF_WEEK, TIME_SLOTS, TIMETABLE_TIME_SLOTS,
    TIMETABLE_DAYS_COUNT, TIMETABLE_PERIODS_COUNT, TIMETABLE_DEFAULT_VALUE,
    TIMETABLE_SKIP_PERIODS, TIMETABLE_ACCESS_DENIED_MESSAGE,
    FREE_TEACHERS_NO_AVAILABLE_TEACHERS_MESSAGE, FREE_TEACHERS_NO_SUBJECT_KNOWLEDGE_MESSAGE,
    TEACHER_FILTER_DISTINCT_ENABLED, TEACHER_FILTER_BY_CLASS, TEACHER_FILTER_BY_SUBJECT_KNOWLEDGE, DATE_FORMAT,
    ATTENDANCE_STANDARD, CIE_STANDARD, TEST_NAME_CHOICES, BREAK_PERIOD, LUNCH_PERIOD,
    CIE_CALCULATION_LIMIT, CIE_DIVISOR
)


# ---------------------------
# Utilities
# ---------------------------
def _calculate_attendance_statistics(attendance_queryset):
    """
    Calculate attendance statistics from an attendance queryset.
    """
    total_students = attendance_queryset.count()
    present_students = attendance_queryset.filter(status=True).count()
    absent_students = total_students - present_students
    attendance_percentage = round(
        (present_students / total_students * 100), 1
    ) if total_students > 0 else 0

    return {
        'total_students': total_students,
        'present_students': present_students,
        'absent_students': absent_students,
        'attendance_percentage': attendance_percentage,
    }


# ---------------------------
# Auth / Dashboard
# ---------------------------
@login_required
def teacher_dashboard(request):
    """
    Teacher dashboard view - only accessible to authenticated teachers
    """
    if not getattr(request.user, 'is_teacher', False):
        messages.error(request, _('Access denied. Teacher credentials required.'))
        return redirect('unified_login')

    try:
        teacher = Teacher.objects.get(user=request.user)
    except Teacher.DoesNotExist:
        messages.error(request, _('Teacher profile not found. Please contact administrator.'))
        return redirect('unified_logout')

    context = {'teacher': teacher, 'user': request.user}
    return render(request, 't_homepage.html', context)


def teacher_logout(request):
    """Redirect to unified logout"""
    return redirect('unified_logout')


@login_required
def index(request):
    """Legacy redirect to dashboard"""
    return redirect('teacher_dashboard')


# ---------------------------
# Class list / filtering
# ---------------------------
@login_required
def t_clas(request, teacher_id, choice):
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    teacher1 = get_object_or_404(Teacher, id=teacher_id)

    # Base queryset (select_related to reduce N+1)
    assignments = (
        Assign.objects.filter(teacher=teacher1)
        .select_related('class_id', 'subject', 'class_id__dept')
    )

    # Filters
    selected_semester = request.GET.get('semester', '')  # "1", "2", ...
    selected_year = request.GET.get('academic_year', '')  # "2024", "2025", ...

    # If both provided, prefer exact match "year.sem" against property `year_sem` (nếu model có)
    if selected_year and selected_semester and selected_semester.isdigit():
        semester_int = int(selected_semester)
        target_year_sem = f"{selected_year}.{semester_int}"
        filtered_ids = []
        for ass in assignments:
            # Ưu tiên thuộc tính year_sem nếu có; fallback so khớp academic_year + semester
            if hasattr(ass, "year_sem"):
                if ass.year_sem == target_year_sem:
                    filtered_ids.append(ass.id)
            else:
                # fallback: academic_year có thể là "2024-2025"
                years = re.findall(r'\b\d{4}\b', str(ass.academic_year))
                if semester_int == 1:
                    display_year = years[0] if len(years) >= 1 else (years[0] if years else "")
                else:
                    display_year = years[1] if len(years) >= 2 else (years[0] if years else "")
                if str(display_year) == selected_year and ass.semester == semester_int:
                    filtered_ids.append(ass.id)
        assignments = assignments.filter(id__in=filtered_ids)

    elif selected_year:
        # Lọc tất cả assignment có academic_year chứa năm này (ví dụ "2024-2025")
        assignments = assignments.filter(academic_year__icontains=selected_year)

    elif selected_semester and selected_semester.isdigit():
        assignments = assignments.filter(semester=int(selected_semester))

    # Ordering
    assignments = assignments.order_by('class_id__dept__name', 'class_id__sem', 'class_id__section', 'subject__name')

    # Pagination
    paginator = Paginator(assignments, 10)
    page = request.GET.get('page')
    try:
        assignments = paginator.page(page)
    except PageNotAnInteger:
        assignments = paginator.page(1)
    except EmptyPage:
        assignments = paginator.page(paginator.num_pages)

    # Options for dropdowns
    all_for_filter = Assign.objects.filter(teacher=teacher1)
    # Extract all 4-digit years from academic_year strings
    years_set = set()
    for year_str in all_for_filter.values_list('academic_year', flat=True).distinct():
        years_found = re.findall(r'\b\d{4}\b', str(year_str))
        years_set.update(years_found)
    available_years = sorted(list(years_set), reverse=True)

    available_semesters = sorted(
        list(all_for_filter.values_list('semester', flat=True).distinct())
    )
    selected_semester_int = int(selected_semester) if selected_semester.isdigit() else None

    context = {
        'teacher1': teacher1,
        'choice': choice,
        'assignments': assignments,
        # tên biến cho template cũ & mới
        'available_years': available_years,
        'academic_years': available_years,
        'available_semesters': available_semesters,
        'semesters': available_semesters if available_semesters else range(1, 4),
        'selected_academic_year': selected_year,
        'selected_year': selected_year,
        'selected_semester': selected_semester,
        'selected_semester_int': selected_semester_int,
    }
    return render(request, 't_clas.html', context)


# ---------------------------
# Exams / Marks
# ---------------------------
@login_required
def t_marks_list(request, assign_id):
    assignment = get_object_or_404(Assign, id=assign_id)

    # Permission check: owner teacher only
    owner_user = getattr(assignment.teacher, 'user', None)
    if owner_user and owner_user != request.user:
        messages.error(request, _('Bạn không có quyền truy cập assignment này!'))
        return redirect('teacher_dashboard')

    exam_sessions_list = ExamSession.objects.filter(assign=assignment)

    if request.method == 'POST' and 'create_exam' in request.POST:
        exam_name = request.POST.get('exam_name')
        if exam_name:
            try:
                new_exam, created = ExamSession.objects.get_or_create(
                    assign=assignment, name=exam_name, defaults={'status': False}
                )
                if created:
                    messages.success(request, _('Bài kiểm tra "%(exam_name)s" đã được tạo thành công!')
                                     % {'exam_name': exam_name})
                else:
                    messages.warning(request, _('Bài kiểm tra "%(exam_name)s" đã tồn tại!')
                                     % {'exam_name': exam_name})
            except Exception as e:
                messages.error(request, _('Lỗi khi tạo bài kiểm tra: %(error)s') % {'error': str(e)})
        else:
            messages.error(request, _('Vui lòng nhập tên bài kiểm tra!'))

        return redirect('t_marks_list', assign_id=assign_id)

    total_exams = exam_sessions_list.count()
    completed_exams = exam_sessions_list.filter(status=True).count()
    pending_exams = total_exams - completed_exams

    context = {
        'assignment': assignment,
        'm_list': exam_sessions_list,
        'exam_names': TEST_NAME_CHOICES,
        'total_exams': total_exams,
        'completed_exams': completed_exams,
        'pending_exams': pending_exams,
    }
    return render(request, 't_marks_list.html', context)


@login_required()
def t_marks_entry(request, marks_c_id):
    with transaction.atomic():
        exam_session = get_object_or_404(ExamSession, id=marks_c_id)
        assignment = exam_session.assign
        subject = assignment.subject
        class_obj = assignment.class_id

        # Lấy toàn bộ HS trong lớp (kèm cờ đk môn & điểm hiện có)
        all_students_in_class = class_obj.student_set.all().order_by('name')

        # Pagination
        from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
        students_total = all_students_in_class.count()
        paginator = Paginator(all_students_in_class, 20)
        page = request.GET.get('page')
        try:
            students_page = paginator.page(page)
        except PageNotAnInteger:
            students_page = paginator.page(1)
        except EmptyPage:
            students_page = paginator.page(paginator.num_pages)

        # Prefill current marks & registration flag
        for student in students_page:
            try:
                student_subject = StudentSubject.objects.get(student=student, subject=subject)
                latest_mark = (
                    student_subject.marks_set.filter(name=exam_session.name)
                    .order_by('-id').first()
                )
                student.current_mark = latest_mark.marks1 if latest_mark else 0
                student.is_registered = True
            except StudentSubject.DoesNotExist:
                student.current_mark = 0
                student.is_registered = False

        context = {
            'ass': assignment,
            'c': class_obj,
            'mc': exam_session,
            'students_total': students_total,
            'students_page': students_page,
            'dept1': class_obj.dept,
            'is_edit_mode': False,
        }
        return render(request, 't_marks_entry.html', context)


@login_required()
def marks_confirm(request, marks_c_id):
    with transaction.atomic():
        exam_session = get_object_or_404(ExamSession, id=marks_c_id)
        assignment = exam_session.assign
        subject = assignment.subject
        class_object = assignment.class_id

        # Chỉ xử lý các input có gửi điểm và học sinh đã đăng ký môn
        for student in class_object.student_set.all():
            student_mark = request.POST.get(student.USN)
            if student_mark is None:
                continue
            try:
                student_subject = StudentSubject.objects.get(subject=subject, student=student)
            except StudentSubject.DoesNotExist:
                continue

            marks_instance, _ = student_subject.marks_set.get_or_create(name=exam_session.name)
            marks_instance.marks1 = student_mark
            marks_instance.save()

        exam_session.status = True
        exam_session.save()

    return HttpResponseRedirect(reverse('t_marks_list', args=(assignment.id,)))


@login_required()
def edit_marks(request, marks_c_id):
    # Reuse UI của t_marks_entry nhưng đặt is_edit_mode = True
    with transaction.atomic():
        exam_session = get_object_or_404(ExamSession, id=marks_c_id)
        assignment = exam_session.assign
        subject = assignment.subject
        class_object = assignment.class_id

        all_students_in_class = class_object.student_set.all().order_by('name')

        from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
        students_total = all_students_in_class.count()
        paginator = Paginator(all_students_in_class, 20)
        page = request.GET.get('page')
        try:
            students_page = paginator.page(page)
        except PageNotAnInteger:
            students_page = paginator.page(1)
        except EmptyPage:
            students_page = paginator.page(paginator.num_pages)

        for student in students_page:
            try:
                student_subject = StudentSubject.objects.get(student=student, subject=subject)
                latest_mark = (
                    student_subject.marks_set.filter(name=exam_session.name)
                    .order_by('-id').first()
                )
                student.current_mark = latest_mark.marks1 if latest_mark else 0
                student.is_registered = True
            except StudentSubject.DoesNotExist:
                student.current_mark = 0
                student.is_registered = False

        context = {
            'ass': assignment,
            'c': class_object,
            'mc': exam_session,
            'students_total': students_total,
            'students_page': students_page,
            'dept1': class_object.dept,
            'is_edit_mode': True,
        }
        return render(request, 't_marks_entry.html', context)


# ---------------------------
# Timetable
# ---------------------------
@login_required()
def t_timetable(request, teacher_id):
    with transaction.atomic():
        teacher = get_object_or_404(Teacher, id=teacher_id)

        if teacher.user != request.user and not (
            getattr(request.user, 'is_staff', False) or getattr(request.user, 'is_superuser', False)
        ):
            messages.error(request, _(TIMETABLE_ACCESS_DENIED_MESSAGE))
            return redirect('teacher_dashboard')

        asst = AssignTime.objects.filter(assign__teacher_id=teacher_id)

        # Filters
        year = request.GET.get('academic_year')
        sem = request.GET.get('semester')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')

        today = timezone.now().date()
        if not year:
            year = determine_academic_year_start(today)
        if not sem:
            sem = str(determine_semester(today))

        # Date range by semester (auto)
        if not (start_date and end_date) and year and sem and sem.isdigit():
            from utils.date_utils import get_semester_date_range
            try:
                start, end = get_semester_date_range(year, int(sem))
                start_date = start.strftime("%Y-%m-%d")
                end_date = end.strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                start = end = None
        else:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
                end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
            except ValueError:
                start = end = None

        if year:
            asst = asst.filter(assign__academic_year__icontains=year)
        if sem and sem.isdigit():
            asst = asst.filter(assign__semester=int(sem))

        # Week navigation
        week_start_str = request.GET.get('week_start')
        try:
            base_date = datetime.strptime(week_start_str, "%Y-%m-%d").date() if week_start_str else today
        except ValueError:
            base_date = today

        monday_start = base_date - timedelta(days=base_date.weekday())
        days = [day[0] for day in DAYS_OF_WEEK]
        day_to_date = {
            day_name: (monday_start + timedelta(days=idx)).strftime('%Y-%m-%d')
            for idx, day_name in enumerate(days)
        }
        prev_week_start = (monday_start - timedelta(days=7)).strftime('%Y-%m-%d')
        next_week_start = (monday_start + timedelta(days=7)).strftime('%Y-%m-%d')

        # Date range filter against this week’s days
        if start and end:
            day_dates = {d: datetime.strptime(day_to_date[d], "%Y-%m-%d").date() for d in days}
            asst = asst.filter(day__in=[d for d, dt in day_dates.items() if start <= dt <= end])

        # Build slot list with breaks
        base_slots = [slot[0] for slot in TIME_SLOTS]
        time_slots = []
        for slot in base_slots:
            time_slots.append(slot)
            if slot == '9:30 - 10:30':
                time_slots.append(BREAK_PERIOD)
            elif slot == '12:40 - 1:30':
                time_slots.append(LUNCH_PERIOD)

        timetable = {day: {slot: None for slot in time_slots} for day in days}
        for at in asst:
            if at.day in timetable and at.period in timetable[at.day]:
                timetable[at.day][at.period] = {
                    'subject': at.assign.subject,
                    'teacher': at.assign.teacher,
                    'assignment': at.assign
                }

        year_options = (
            AssignTime.objects
            .filter(assign__teacher_id=teacher_id)
            .values_list('assign__academic_year', flat=True)
            .distinct()
            .order_by('assign__academic_year')
        )

        context = {
            'teacher': teacher,
            'timetable': timetable,
            'days': days,
            'day_to_date': day_to_date,
            'time_slots': time_slots,
            'week_start': monday_start.strftime('%Y-%m-%d'),
            'prev_week_start': prev_week_start,
            'next_week_start': next_week_start,
            'academic_year': year,
            'semester': sem,
            'year_options': list(year_options),
            'today': today.strftime('%Y-%m-%d'),
            'start_date': start_date or '',
            'end_date': end_date or '',
        }
        return render(request, 't_timetable.html', context)


# ---------------------------
# Find free teachers for a slot
# ---------------------------
@login_required()
def free_teachers(request, asst_id):
    with transaction.atomic():
        asst = get_object_or_404(AssignTime, id=asst_id)
        required_subject = asst.assign.subject

        t_list = Teacher.objects.filter(assign__class_id=asst.assign.class_id).distinct()

        ft_list = []
        teachers_without_knowledge = []

        for t in t_list:
            at_list = AssignTime.objects.filter(assign__teacher=t)
            is_busy = any(at.period == asst.period and at.day == asst.day for at in at_list)
            has_subject_knowledge = t.assign_set.filter(subject=required_subject).exists()

            if not is_busy:
                (ft_list if has_subject_knowledge else teachers_without_knowledge).append(t)

        if not ft_list:
            messages.warning(request, _(FREE_TEACHERS_NO_AVAILABLE_TEACHERS_MESSAGE))

        return render(request, 'free_teachers.html', {
            'ft_list': ft_list,
            'required_subject': required_subject,
            'assignment_time': asst,
            'teachers_without_knowledge': teachers_without_knowledge,
            'total_teachers_checked': len(t_list),
            'available_teachers_count': len(ft_list)
        })


# ---------------------------
# Attendance (dates, mark, edit, view)
# ---------------------------
@login_required
def t_class_date(request, assign_id):
    assign = get_object_or_404(Assign, id=assign_id)
    now = timezone.now()
    att_list = assign.attendanceclass_set.all().order_by('-date')
    selected_assc = None
    class_obj = assign.class_id
    has_students = class_obj.student_set.exists()
    students = class_obj.student_set.all() if has_students else []

    # Append stats to each AttendanceClass
    att_list_with_stats = []
    for att_class in att_list:
        attendance_records = Attendance.objects.filter(attendanceclass=att_class, subject=assign.subject)
        stats = _calculate_attendance_statistics(attendance_records)
        att_class.total_students = stats['total_students']
        att_class.present_students = stats['present_students']
        att_class.absent_students = stats['absent_students']
        att_class.attendance_percentage = stats['attendance_percentage']
        att_list_with_stats.append(att_class)

    if request.method == 'POST' and 'create_attendance' in request.POST:
        date_str = request.POST.get('attendance_date')
        try:
            attendance_date = timezone.datetime.strptime(date_str, DATE_FORMAT).date()
            if not AttendanceClass.objects.filter(assign=assign, date=attendance_date).exists():
                with transaction.atomic():
                    AttendanceClass.objects.create(assign=assign, date=attendance_date, status=0)  # Not Marked
            selected_assc = AttendanceClass.objects.get(assign=assign, date=attendance_date)
        except ValueError:
            messages.error(request, _('Invalid date format. Please use YYYY-MM-DD.'))
            return redirect('t_class_date', assign_id=assign.id)

    elif request.method == 'POST' and 'confirm_attendance' in request.POST:
        assc_id = request.POST.get('assc_id')
        assc = get_object_or_404(AttendanceClass, id=assc_id)
        subject = assign.subject

        with transaction.atomic():
            for student in students:
                status_str = request.POST.get(student.USN)
                status = status_str == 'present'
                attendance_obj, created = Attendance.objects.get_or_create(
                    student=student, subject=subject, attendanceclass=assc, date=assc.date,
                    defaults={'status': status}
                )
                if not created:
                    attendance_obj.status = status
                    attendance_obj.save()
            assc.status = 1  # Marked
            assc.save()
        messages.success(request, _('Attendance successfully recorded.'))
        return HttpResponseRedirect(reverse('t_class_date', args=(assign.id,)))

    elif request.method == 'POST' and 'select_attendance' in request.POST:
        assc_id = request.POST.get('assc_id')
        selected_assc = get_object_or_404(AttendanceClass, id=assc_id)

    class_info = {
        'class_id': class_obj.id,
        'department': class_obj.dept.name,
        'section': class_obj.section,
        'semester': class_obj.sem,
        'total_students': len(students),
        'subject': assign.subject.name,
        'subject_code': assign.subject.id,
        'teacher': assign.teacher.name,
    }

    context = {
        'assign': assign,
        'att_list': att_list_with_stats,
        'today': now.date(),
        'selected_assc': selected_assc,
        'c': class_obj,
        'has_students': has_students,
        'students': students,
        'class_info': class_info,
    }
    return render(request, 't_class_date.html', context)


@login_required
def t_attendance(request, ass_c_id):
    assc = get_object_or_404(AttendanceClass, id=ass_c_id)
    assign = assc.assign
    class_obj = assign.class_id
    students = class_obj.student_set.all()
    total_students_in_class = students.count()

    class_info = {
        'class_id': class_obj.id,
        'department': class_obj.dept.name,
        'section': class_obj.section,
        'semester': class_obj.sem,
        'total_students': total_students_in_class,
        'subject': assign.subject.name,
        'subject_code': assign.subject.id,
        'teacher': assign.teacher.name,
        'date': assc.date,
    }

    context = {
        'ass': assign,
        'c': class_obj,
        'assc': assc,
        'students': students,
        'total_students_in_class': total_students_in_class,
        'class_info': class_info,
    }
    return render(request, 't_attendance.html', context)


@login_required
def confirm(request, ass_c_id):
    assc = get_object_or_404(AttendanceClass, id=ass_c_id)
    assign = assc.assign
    subject = assign.subject
    class_obj = assign.class_id
    has_students = class_obj.student_set.exists()
    students = class_obj.student_set.all() if has_students else []

    with transaction.atomic():
        for student in students:
            status_str = request.POST.get(student.USN)
            status = status_str == 'present'
            attendance_obj, created = Attendance.objects.get_or_create(
                student=student, subject=subject, attendanceclass=assc, date=assc.date,
                defaults={'status': status}
            )
            if not created:
                attendance_obj.status = status
                attendance_obj.save()
        assc.status = 1
        assc.save()

    messages.success(request, _('Attendance successfully recorded.'))
    return HttpResponseRedirect(reverse('t_class_date', args=(assc.assign.id,)))


@login_required
def edit_att(request, ass_c_id):
    assc = get_object_or_404(AttendanceClass, id=ass_c_id)
    assign = assc.assign
    subject = assign.subject
    att_list = Attendance.objects.filter(attendanceclass=assc, subject=subject)
    class_obj = assign.class_id

    stats = _calculate_attendance_statistics(att_list)

    context = {
        'assc': assc,
        'att_list': att_list,
        'assign': assign,
        'class_obj': class_obj,
        'total_students': stats['total_students'],
        'present_students': stats['present_students'],
        'absent_students': stats['absent_students'],
    }
    return render(request, 't_edit_att.html', context)


@login_required
def view_att(request, ass_c_id):
    assc = get_object_or_404(AttendanceClass, id=ass_c_id)
    assign = assc.assign
    subject = assign.subject
    att_list = Attendance.objects.filter(attendanceclass=assc, subject=subject)
    class_obj = assign.class_id

    stats = _calculate_attendance_statistics(att_list)

    class_info = {
        'class_id': class_obj.id,
        'department': class_obj.dept.name,
        'section': class_obj.section,
        'semester': class_obj.sem,
        'subject': assign.subject.name,
        'subject_code': assign.subject.id,
        'teacher': assign.teacher.name,
        'date': assc.date,
    }

    context = {
        'assc': assc,
        'att_list': att_list,
        'assign': assign,
        'total_students': stats['total_students'],
        'present_students': stats['present_students'],
        'absent_students': stats['absent_students'],
        'attendance_percentage': stats['attendance_percentage'],
        'class_info': class_info,
    }
    return render(request, 't_view_att.html', context)


# ---------------------------
# Report
# ---------------------------
@login_required()
def t_report(request, assign_id):
    ass = get_object_or_404(Assign, id=assign_id)
    sc_list = []

    class_obj = ass.class_id
    subject_obj = ass.subject

    good_attendance_count = 0
    good_cie_count = 0
    need_support_count = 0

    for stud in class_obj.student_set.all():
        student_subjects = StudentSubject.objects.filter(student=stud, subject=subject_obj)
        if student_subjects.exists():
            student_subject = student_subjects.first()
            sc_list.append(student_subject)

            try:
                attendance = student_subject.get_attendance()
            except Exception:
                attendance = 0

            try:
                cie = student_subject.get_cie()
            except Exception:
                cie = 0

            if attendance >= ATTENDANCE_STANDARD:
                good_attendance_count += 1
            if cie >= CIE_STANDARD:
                good_cie_count += 1
            if attendance < ATTENDANCE_STANDARD or cie < CIE_STANDARD:
                need_support_count += 1

    total_students = len(sc_list)
    pass_rate = 100 if total_students == 0 else round((total_students - need_support_count) / total_students * 100)

    context = {
        'sc_list': sc_list,
        'class_obj': class_obj,
        'subject_obj': subject_obj,
        'assignment': ass,
        'good_attendance_count': good_attendance_count,
        'good_cie_count': good_cie_count,
        'need_support_count': need_support_count,
        'pass_rate': pass_rate,
        'ATTENDANCE_STANDARD': ATTENDANCE_STANDARD,
        'CIE_STANDARD': CIE_STANDARD,
        'attendance_success_label': f"≥{ATTENDANCE_STANDARD}%",
        'attendance_danger_label': f"<{ATTENDANCE_STANDARD}%",
        'cie_success_label': f"≥{CIE_STANDARD}",
        'cie_danger_label': f"<{CIE_STANDARD}",
    }

    return render(request, 't_report.html', context)


# ---------------------------
# Aggregated view: students & totals
# ---------------------------
@login_required
def view_students(request, assign_id):
    """
    View danh sách sinh viên và điểm tổng/CIE/điểm danh cho một assignment
    """
    assignment = get_object_or_404(Assign, id=assign_id)

    owner_user = getattr(assignment.teacher, 'user', None)
    if owner_user and owner_user != request.user:
        messages.error(request, _('Bạn không có quyền truy cập assignment này!'))
        return redirect('teacher_dashboard')

    students = assignment.class_id.student_set.all().order_by('name')

    students_data = []
    for student in students:
        try:
            student_subject = StudentSubject.objects.get(student=student, subject=assignment.subject)
            marks = student_subject.marks_set.all().order_by('name')
            total_marks = sum(mark.marks1 for mark in marks)

            attendance_records = Attendance.objects.filter(student=student, subject=assignment.subject)
            total_classes = attendance_records.count()
            attended_classes = attendance_records.filter(status=True).count()
            attendance_percentage = round((attended_classes / total_classes * 100), 2) if total_classes > 0 else 0

            marks_list = [m.marks1 for m in marks]
            cie_score = math.ceil(sum(marks_list[:CIE_CALCULATION_LIMIT]) / CIE_DIVISOR) if marks_list else 0
            is_registered = True
        except StudentSubject.DoesNotExist:
            marks = []
            total_marks = 0
            attendance_percentage = 0
            cie_score = 0
            total_classes = 0
            attended_classes = 0
            is_registered = False

        students_data.append({
            'student': student,
            'marks': marks,
            'total_marks': total_marks,
            'attendance_percentage': attendance_percentage,
            'cie_score': cie_score,
            'total_classes': total_classes,
            'attended_classes': attended_classes,
            'is_registered': is_registered,
        })

    # Pagination
    from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
    paginator = Paginator(students_data, 25)
    page = request.GET.get('page')
    try:
        students_page = paginator.page(page)
    except PageNotAnInteger:
        students_page = paginator.page(1)
    except EmptyPage:
        students_page = paginator.page(paginator.num_pages)

    context = {
        'assignment': assignment,
        'students_page': students_page,
        'students_total': len(students_data),
        'class_obj': assignment.class_id,
        'subject': assignment.subject,
    }
    return render(request, 't_view_students.html', context)
