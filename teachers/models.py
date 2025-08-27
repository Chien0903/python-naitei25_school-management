from django.db import models
import math
from django.core.validators import MinValueValidator, MaxValueValidator
from utils.constant import (
    # Choices
    SEX_CHOICES, TIME_SLOTS, DAYS_OF_WEEK, TEST_NAME_CHOICES,
    # Field Lengths
    TEACHER_ID_MAX_LENGTH, USER_NAME_MAX_LENGTH, USER_SEX_MAX_LENGTH,
    USER_ADDRESS_MAX_LENGTH, USER_PHONE_MAX_LENGTH,
    ASSIGN_TIME_PERIOD_MAX_LENGTH, ASSIGN_TIME_DAY_MAX_LENGTH,
    TEST_NAME_MAX_LENGTH,
    # Default Values
    DEFAULT_SEX, DEFAULT_EMPTY_STRING, DEFAULT_DEPT_ID,
    DEFAULT_STATUS_FALSE, DEFAULT_ATTENDANCE_STATUS, DEFAULT_MARKS_VALUE,
    # Business Logic Constants
    SEMESTER_END_EXAM_TOTAL_MARKS, OTHER_EXAM_TOTAL_MARKS, SEMESTER_END_EXAM_NAME,
    FIRST_CHOICE_INDEX, TEACHER_ATTRIBUTE, ADMINS_USER_MODEL, ADMINS_DEPT_MODEL,
    ADMINS_CLASS_MODEL, ADMINS_SUBJECT_MODEL, STUDENTS_STUDENT_SUBJECT_MODEL,
    # Validation Constants
    MIN_MARKS_VALUE, MAX_MARKS_VALUE,
    # Verbose Names
    ATTENDANCE_VERBOSE_NAME, ATTENDANCE_VERBOSE_NAME_PLURAL
)


class Teacher(models.Model):
    user = models.OneToOneField(
        ADMINS_USER_MODEL, on_delete=models.RESTRICT, null=True)
    id = models.CharField(primary_key=True, max_length=TEACHER_ID_MAX_LENGTH)
    dept = models.ForeignKey(
        ADMINS_DEPT_MODEL, on_delete=models.RESTRICT, default=DEFAULT_DEPT_ID)
    name = models.CharField(max_length=USER_NAME_MAX_LENGTH)
    sex = models.CharField(max_length=USER_SEX_MAX_LENGTH, choices=SEX_CHOICES, default=DEFAULT_SEX)
    DOB = models.DateField(default=DEFAULT_EMPTY_STRING)
    address = models.TextField(max_length=USER_ADDRESS_MAX_LENGTH, default=DEFAULT_EMPTY_STRING)
    phone = models.CharField(max_length=USER_PHONE_MAX_LENGTH, default=DEFAULT_EMPTY_STRING)

    def __str__(self):
        return self.name


class Assign(models.Model):
    class_id = models.ForeignKey(ADMINS_CLASS_MODEL, on_delete=models.RESTRICT)
    subject = models.ForeignKey(ADMINS_SUBJECT_MODEL, on_delete=models.RESTRICT)
    teacher = models.ForeignKey(Teacher, on_delete=models.RESTRICT)
    academic_year = models.CharField(max_length=20, default="2024-2025")
    semester = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = (('subject', 'class_id', 'teacher', 'academic_year', 'semester'),)
    
    def clean(self):
        """Validate model fields."""
        from django.core.exceptions import ValidationError
        
        # Validate academic year format
        try:
            self._parse_academic_year()
        except ValueError as e:
            raise ValidationError({'academic_year': str(e)})
        
        # Validate semester is valid (1, 2, or 3)
        if self.semester not in [1, 2, 3]:
            raise ValidationError({'semester': 'Semester must be 1, 2, or 3.'})
    
    def save(self, *args, **kwargs):
        """Override save to run validation."""
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def year_sem(self):
        """Return display format 'YYYY.S' from academic_year and semester.
        For academic years like '2024-2025':
        - Semester 1: 2024.1 (Sep-Jan belongs to first year)
        - Semester 2: 2025.2 (Feb-Jun belongs to second year) 
        - Semester 3: 2025.3 (Jul-Aug belongs to second year)
        """
        try:
            # Parse academic year with validation
            display_year = self._parse_academic_year()
            return f"{display_year}.{self.semester}"
        except ValueError as e:
            # Fallback to original format if parsing fails
            return f"{self.academic_year}.{self.semester}"
    
    def _parse_academic_year(self):
        """Parse and validate academic year format.
        
        Supported formats:
        - "2024-2025" (two consecutive years)
        - "2024" (single year)
        
        Returns:
            str: The appropriate year for the current semester
            
        Raises:
            ValueError: If academic year format is invalid
        """
        import re
        from datetime import datetime
        
        year_str = str(self.academic_year).strip()
        
        # Pattern for "YYYY-YYYY" format
        range_pattern = r'^(\d{4})-(\d{4})$'
        # Pattern for single year "YYYY"
        single_pattern = r'^(\d{4})$'
        
        range_match = re.match(range_pattern, year_str)
        if range_match:
            first_year, second_year = range_match.groups()
            first_year_int = int(first_year)
            second_year_int = int(second_year)
            
            # Validate year range
            if second_year_int != first_year_int + 1:
                raise ValueError(f"Invalid academic year range: {year_str}. Second year must be consecutive to first year.")
            
            # Validate years are reasonable (not too far in past/future)
            current_year = datetime.now().year
            if not (2000 <= first_year_int <= current_year + 10):
                raise ValueError(f"Invalid academic year: {first_year}. Year should be between 2000 and {current_year + 10}.")
            
            # Determine which year this semester belongs to
            if self.semester == 1:
                return first_year
            else:  # semester 2 or 3
                return second_year
        
        single_match = re.match(single_pattern, year_str)
        if single_match:
            year = single_match.group(1)
            year_int = int(year)
            
            # Validate year is reasonable
            current_year = datetime.now().year
            if not (2000 <= year_int <= current_year + 10):
                raise ValueError(f"Invalid academic year: {year}. Year should be between 2000 and {current_year + 10}.")
            
            return year
        
        # If no pattern matches, raise error
        raise ValueError(f"Invalid academic year format: {year_str}. Expected formats: 'YYYY-YYYY' or 'YYYY'.")

    def __str__(self):
        cl = self.class_id
        sname = self.subject
        te = self.teacher
        return f"{te.name} : {sname.shortname} : {cl} {self.year_sem}"


class AssignTime(models.Model):
    assign = models.ForeignKey(Assign, on_delete=models.RESTRICT)
    period = models.CharField(max_length=ASSIGN_TIME_PERIOD_MAX_LENGTH, choices=TIME_SLOTS, default=DEFAULT_EMPTY_STRING)
    day = models.CharField(max_length=ASSIGN_TIME_DAY_MAX_LENGTH, choices=DAYS_OF_WEEK)

    def __str__(self):
        return f"{self.assign} - {self.day} {self.period}"


class AttendanceClass(models.Model):
    assign = models.ForeignKey(Assign, on_delete=models.RESTRICT)
    date = models.DateField()
    status = models.IntegerField(default=DEFAULT_ATTENDANCE_STATUS)

    class Meta:
        verbose_name = ATTENDANCE_VERBOSE_NAME
        verbose_name_plural = ATTENDANCE_VERBOSE_NAME_PLURAL

    def __str__(self):
        return f"{self.assign} - {self.date}"


class Marks(models.Model):
    student_subject = models.ForeignKey(
        STUDENTS_STUDENT_SUBJECT_MODEL, on_delete=models.RESTRICT)
    name = models.CharField(
        max_length=TEST_NAME_MAX_LENGTH, choices=TEST_NAME_CHOICES, default=TEST_NAME_CHOICES[FIRST_CHOICE_INDEX][FIRST_CHOICE_INDEX])
    marks1 = models.IntegerField(default=DEFAULT_MARKS_VALUE, validators=[
                                 MinValueValidator(MIN_MARKS_VALUE), MaxValueValidator(MAX_MARKS_VALUE)])
    # Thêm phân biệt theo năm học/kỳ để không trộn điểm giữa các kỳ
    academic_year = models.CharField(max_length=20, default="2024-2025")
    semester = models.IntegerField(default=1)

    class Meta:
        unique_together = (('student_subject', 'name', 'academic_year', 'semester'),)

    @property
    def total_marks(self):
        if self.name == SEMESTER_END_EXAM_NAME:
            return SEMESTER_END_EXAM_TOTAL_MARKS
        return OTHER_EXAM_TOTAL_MARKS

    def __str__(self):
        return f"{self.student_subject} - {self.name}: {self.marks1}"


class ExamSession(models.Model):
    assign = models.ForeignKey(Assign, on_delete=models.RESTRICT)
    name = models.CharField(
        max_length=TEST_NAME_MAX_LENGTH, choices=TEST_NAME_CHOICES, default=TEST_NAME_CHOICES[FIRST_CHOICE_INDEX][FIRST_CHOICE_INDEX])
    status = models.BooleanField(default=DEFAULT_STATUS_FALSE)

    class Meta:
        unique_together = (('assign', 'name'),)

    @property
    def total_marks(self):
        if self.name == SEMESTER_END_EXAM_NAME:
            return SEMESTER_END_EXAM_TOTAL_MARKS
        return OTHER_EXAM_TOTAL_MARKS

    def __str__(self):
        return f"{self.assign} - {self.name}"

