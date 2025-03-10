"""
views.py

This module is used to define the method for the path in the urls
"""

from collections import defaultdict
from urllib.parse import parse_qs
import pandas as pd
import json
from datetime import date, datetime, timedelta
from django.utils import timezone
from django.shortcuts import get_object_or_404, render, redirect
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.db.models import Q, ProtectedError
from attendance.methods.group_by import group_by_queryset
from notifications.signals import notify
from base.models import Company
from horilla.decorators import login_required, permission_required
from base.methods import export_data, generate_colors, get_key_instances, sortby
from employee.models import Employee, EmployeeWorkInformation
from base.methods import closest_numbers
from base.methods import generate_pdf
from payroll.models.models import (
    FilingStatus,
    PayrollGeneralSetting,
    Payslip,
    Reimbursement,
    ReimbursementrequestComment,
    WorkRecord,
    Contract,
)
from payroll.forms.forms import (
    ContractForm,
    ReimbursementrequestCommentForm,
    WorkRecordForm,
)
from payroll.models.tax_models import PayrollSettings
from payroll.forms.component_forms import ContractExportFieldForm, PayrollSettingsForm
from payroll.methods.methods import save_payslip
from django.utils.translation import gettext_lazy as _
from payroll.filters import ContractFilter, ContractReGroup, PayslipFilter
from payroll.methods.methods import paginator_qry

# Create your views here.

status_choices = {
    "draft": _("Draft"),
    "review_ongoing": _("Review Ongoing"),
    "confirmed": _("Confirmed"),
    "paid": _("Paid"),
}


def get_language_code(request):
    scale_x_text = _("Name of Employees")
    scale_y_text = _("Amount")
    response = {"scale_x_text": scale_x_text, "scale_y_text": scale_y_text}
    return JsonResponse(response)


@login_required
@permission_required("payroll.add_contract")
def contract_create(request):
    """
    Contract create view
    """
    form = ContractForm()
    if request.method == "POST":
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, _("Contract Created"))
            return redirect(contract_view)
    return render(request, "payroll/common/form.html", {"form": form})


@login_required
@permission_required("payroll.change_contract")
def contract_update(request, contract_id, **kwargs):
    """
    Update an existing contract.

    Args:
        request: The HTTP request object.
        contract_id: The ID of the contract to update.

    Returns:
        If the request method is POST and the form is valid, redirects to the contract view.
        Otherwise, renders the contract update form.

    """
    contract = Contract.objects.filter(id=contract_id).first()
    if not contract:
        messages.info(request, _("The contract could not be found."))
        return redirect(contract_view)
    contract_form = ContractForm(instance=contract)
    if request.method == "POST":
        contract_form = ContractForm(request.POST, request.FILES, instance=contract)
        if contract_form.is_valid():
            contract_form.save()
            messages.success(request, _("Contract updated"))
            return redirect(contract_view)
    return render(
        request,
        "payroll/common/form.html",
        {
            "form": contract_form,
        },
    )


@login_required
@permission_required("payroll.change_contract")
def contract_status_update(request, contract_id):
    if request.method == "POST":
        contract = Contract.objects.get(id=contract_id)
        if request.POST.get("view"):
            status = request.POST.get("status")
            if status in dict(contract.CONTRACT_STATUS_CHOICES).keys():
                contract.contract_status = request.POST.get("status")
                contract.save()
                messages.success(
                    request, _("The contract status has been updated successfully.")
                )
            else:
                messages.warning(
                    request, _("You selected the wrong option for contract status.")
                )
            return redirect(contract_filter)
        contract_form = ContractForm(request.POST, request.FILES, instance=contract)
        if contract_form.is_valid():
            contract_form.save()
            messages.success(request, _("Contract status updated"))
        else:
            for errors in contract_form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return HttpResponse("<script>window.location.reload()</script>")


@login_required
@permission_required("payroll.change_contract")
def update_contract_filing_status(request, contract_id):
    if request.method == "POST":
        contract = get_object_or_404(Contract, id=contract_id)
        filing_status_id = request.POST.get("filing_status")
        try:
            filing_status = (
                FilingStatus.objects.get(id=int(filing_status_id))
                if filing_status_id
                else None
            )
            contract.filing_status = filing_status
            messages.success(
                request, _("The employee filing status has been updated successfully.")
            )
        except (ValueError, OverflowError, FilingStatus.DoesNotExist):
            messages.warning(
                request, _("You selected the wrong option for filing status.")
            )
        contract.save()
        return redirect(contract_filter)


@login_required
@permission_required("payroll.delete_contract")
def contract_delete(request, contract_id):
    """
    Delete a contract.

    Args:
        contract_id: The ID of the contract to delete.

    Returns:
        Redirects to the contract view after successfully deleting the contract.

    """
    try:
        Contract.objects.get(id=contract_id).delete()
        messages.success(request, _("Contract deleted"))
        request_path = request.path.split("/")
        if "delete-contract-modal" in request_path:
            return HttpResponse("<script>window.location.reload();</script>")
        else:
            return redirect(f"/payroll/contract-filter?{request.GET.urlencode()}")
    except Contract.DoesNotExist:
        messages.error(request, _("Contract not found."))
    except ProtectedError:
        messages.error(request, _("You cannot delete this contract."))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@permission_required("payroll.view_contract")
def contract_view(request):
    """
    Contract view method
    """

    contracts = Contract.objects.all()
    if contracts.exists():
        template = "payroll/contract/contract_view.html"
    else:
        template = "payroll/contract/contract_empty.html"

    field = request.GET.get("field")
    contracts = paginator_qry(contracts, request.GET.get("page"))
    contract_ids_json = json.dumps([instance.id for instance in contracts.object_list])
    filter_form = ContractFilter(request.GET)
    export_filter = ContractFilter(request.GET)
    export_column = ContractExportFieldForm()
    context = {
        "contracts": contracts,
        "f": filter_form,
        "export_filter": export_filter,
        "export_column": export_column,
        "contract_ids": contract_ids_json,
        "gp_fields": ContractReGroup.fields,
    }

    return render(request, template, context)


@login_required
@permission_required("payroll.view_contract")
def view_single_contract(request, contract_id):
    """
    Renders a single contract view page.

    Parameters:
    - request (HttpRequest): The HTTP request object.
    - contract_id (int): The ID of the contract to view.

    Returns:
        The rendered contract single view page.

    """
    dashboard = ""
    if request.GET.get("dashboard"):
        dashboard = request.GET.get("dashboard")
    contract = Contract.objects.get(id=contract_id)
    context = {
        "contract": contract,
        "dashboard": dashboard,
    }
    contract_ids_json = request.GET.get("instances_ids")
    if contract_ids_json:
        contract_ids = json.loads(contract_ids_json)
        previous_id, next_id = closest_numbers(contract_ids, contract_id)
        context["next"] = next_id
        context["previous"] = previous_id
        context["contract_ids"] = contract_ids_json
    return render(request, "payroll/contract/contract_single_view.html", context)


@login_required
@permission_required("payroll.view_contract")
def contract_filter(request):
    """
    Filter contracts based on the provided query parameters.

    Args:
        request: The HTTP request object containing the query parameters.

    Returns:
        Renders the contract list template with the filtered contracts.

    """
    query_string = request.GET.urlencode()
    contracts_filter = ContractFilter(request.GET)
    template = "payroll/contract/contract_list.html"
    contracts = contracts_filter.qs
    field = request.GET.get("field")

    if field != "" and field is not None:
        contracts = group_by_queryset(contracts, field, request.GET.get("page"), "page")
        list_values = [entry["list"] for entry in contracts]
        id_list = []
        for value in list_values:
            for instance in value.object_list:
                id_list.append(instance.id)

        contract_ids_json = json.dumps(list(id_list))
        template = "payroll/contract/group_by.html"

    else:
        contracts = paginator_qry(contracts, request.GET.get("page"))
        contract_ids_json = json.dumps(
            [instance.id for instance in contracts.object_list]
        )

    contracts = sortby(request, contracts, "orderby")
    contracts = paginator_qry(contracts, request.GET.get("page"))
    data_dict = parse_qs(query_string)
    get_key_instances(Contract, data_dict)
    keys_to_remove = [key for key, value in data_dict.items() if value == ["unknown"]]
    for key in keys_to_remove:
        data_dict.pop(key)
    if "contract_status" in data_dict:
        status_list = data_dict["contract_status"]
        if len(status_list) > 1:
            data_dict["contract_status"] = [status_list[-1]]
    return render(
        request,
        template,
        {
            "contracts": contracts,
            "pd": query_string,
            "filter_dict": data_dict,
            "contract_ids": contract_ids_json,
            "field": field,
        },
    )


@login_required
@permission_required("payroll.view_workrecord")
def work_record_create(request):
    """
    Work record create view
    """
    form = WorkRecordForm()

    context = {"form": form}
    if request.POST:
        form = WorkRecordForm(request.POST)
        if form.is_valid():
            form.save()
        else:
            context["form"] = form
    return render(request, "payroll/work_record/work_record_create.html", context)


@login_required
@permission_required("payroll.view_workrecord")
def work_record_view(request):
    """
    Work record view method
    """
    contracts = WorkRecord.objects.all()
    context = {"contracts": contracts}
    return render(request, "payroll/work_record/work_record_view.html", context)


@login_required
@permission_required("payroll.workrecord")
def work_record_employee_view(request):
    """
    Work record by employee view method
    """
    current_month_start_date = datetime.now().replace(day=1)
    next_month_start_date = current_month_start_date + timedelta(days=31)
    current_month_records = WorkRecord.objects.filter(
        start_datetime__gte=current_month_start_date,
        start_datetime__lt=next_month_start_date,
    ).order_by("start_datetime")
    current_date = timezone.now().date()
    current_month = current_date.strftime("%B")
    start_of_month = current_date.replace(day=1)
    employees = Employee.objects.all()

    current_month_dates_list = [
        datetime.now().replace(day=day).date() for day in range(1, 32)
    ]

    context = {
        "days": range(1, 32),
        "employees": employees,
        "current_date": current_date,
        "current_month": current_month,
        "start_of_month": start_of_month,
        "current_month_dates_list": current_month_dates_list,
        "work_records": current_month_records,
    }

    return render(
        request, "payroll/work_record/work_record_employees_view.html", context
    )


@login_required
@permission_required("payroll.view_payrollsettings")
def settings(request):
    """
    This method is used to render settings template
    """
    instance = PayrollSettings.objects.first()
    form = PayrollSettingsForm(instance=instance)
    if request.method == "POST":
        form = PayrollSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, _("Payroll settings updated."))
    return render(request, "payroll/settings/payroll_settings.html", {"form": form})


@login_required
@permission_required("payroll.change_payslip")
def update_payslip_status(request, payslip_id):
    """
    This method is used to update the payslip confirmation status
    """
    status = request.POST.get("status")
    view = request.POST.get("view")
    payslip = Payslip.objects.filter(id=payslip_id).first()
    if payslip:
        payslip.status = status
        payslip.save()
        messages.success(request, _("Payslip status updated"))
    else:
        messages.error(request, _("Payslip not found"))
    if view:
        from .component_views import filter_payslip

        return redirect(filter_payslip)
    data = payslip.pay_head_data
    data["employee"] = payslip.employee_id
    data["payslip"] = payslip
    data["json_data"] = data.copy()
    data["json_data"]["employee"] = payslip.employee_id.id
    data["json_data"]["payslip"] = payslip.id
    data["instance"] = payslip
    return render(request, "payroll/payslip/individual_payslip_summery.html", data)


def update_payslip_status_no_id(request):
    """
    This method is used to update the payslip confirmation status
    """
    message = {"type": "success", "message": "Payslip status updated."}
    if request.method == "POST":
        ids_json = request.POST["ids"]
        ids = json.loads(ids_json)
        status = request.POST["status"]
        slips = Payslip.objects.filter(id__in=ids)
        slips.update(status=status)
        message = {
            "type": "success",
            "message": f"{slips.count()} Payslips status updated.",
        }
    return JsonResponse(message)


@login_required
@permission_required("payroll.change_payslip")
def bulk_update_payslip_status(request):
    """
    This method is used to update payslip status when generating payslip through
    generate payslip method
    """
    json_data = request.GET["json_data"]
    pay_data = json.loads(json_data)
    status = request.GET["status"]

    for json_entry in pay_data:
        data = json.loads(json_entry)
        emp_id = data["employee"]
        employee = Employee.objects.get(id=emp_id)

        payslip_kwargs = {
            "employee_id": employee,
            "start_date": data["start_date"],
            "end_date": data["end_date"],
        }
        filtered_instance = Payslip.objects.filter(**payslip_kwargs).first()
        instance = filtered_instance if filtered_instance is not None else Payslip()

        instance.employee_id = employee
        instance.start_date = data["start_date"]
        instance.end_date = data["end_date"]
        instance.status = status
        instance.basic_pay = data["basic_pay"]
        instance.contract_wage = data["contract_wage"]
        instance.gross_pay = data["gross_pay"]
        instance.deduction = data["total_deductions"]
        instance.net_pay = data["net_pay"]
        instance.pay_head_data = data
        instance.save()

    return JsonResponse({"type": "success", "message": "Payslips status updated"})


@login_required
# @permission_required("payroll.view_payslip")
def view_created_payslip(request, payslip_id, **kwargs):
    """
    This method is used to view the saved payslips
    """
    payslip = Payslip.objects.filter(id=payslip_id).first()
    if payslip is not None and (
        request.user.has_perm("payroll.view_payslip")
        or payslip.employee_id.employee_user_id == request.user
    ):
        # the data must be dictionary in the payslip model for the json field
        data = payslip.pay_head_data
        data["employee"] = payslip.employee_id
        data["payslip"] = payslip
        data["json_data"] = data.copy()
        data["json_data"]["employee"] = payslip.employee_id.id
        data["json_data"]["payslip"] = payslip.id
        data["instance"] = payslip
        return render(request, "payroll/payslip/individual_payslip.html", data)
    return render(request, "404.html")


@login_required
@permission_required("payroll.delete_payslip")
def delete_payslip(request, payslip_id):
    """
    This method is used to delete payslip instances
    Args:
        payslip_id (int): Payslip model instance id
    """
    from .component_views import filter_payslip

    try:
        Payslip.objects.get(id=payslip_id).delete()
        messages.success(request, _("Payslip deleted"))
    except Payslip.DoesNotExist:
        messages.error(request, _("Payslip not found."))
    except ProtectedError:
        messages.error(request, _("Something went wrong"))
    return redirect(filter_payslip)


@login_required
@permission_required("payroll.add_contract")
def contract_info_initial(request):
    """
    This is an ajax method to return json response to auto fill the contract
    form fields
    """
    employee_id = request.GET["employee_id"]
    work_info = EmployeeWorkInformation.objects.filter(employee_id=employee_id).first()
    response_data = {
        "department": (
            work_info.department_id.id if work_info.department_id is not None else ""
        ),
        "job_position": (
            work_info.job_position_id.id
            if work_info.job_position_id is not None
            else ""
        ),
        "job_role": (
            work_info.job_role_id.id if work_info.job_role_id is not None else ""
        ),
        "shift": work_info.shift_id.id if work_info.shift_id is not None else "",
        "work_type": (
            work_info.work_type_id.id if work_info.work_type_id is not None else ""
        ),
        "wage": work_info.basic_salary,
        "contract_start_date": work_info.date_joining if work_info.date_joining else "",
        "contract_end_date": (
            work_info.contract_end_date if work_info.contract_end_date else ""
        ),
    }
    return JsonResponse(response_data)


@login_required
@permission_required("payroll.view_contract")
def view_payroll_dashboard(request):
    """
    Dashboard rendering views
    """

    paid = Payslip.objects.filter(status="paid")
    posted = Payslip.objects.filter(status="confirmed")
    review_ongoing = Payslip.objects.filter(status="review_ongoing")
    draft = Payslip.objects.filter(status="draft")
    context = {
        "paid": paid,
        "posted": posted,
        "review_ongoing": review_ongoing,
        "draft": draft,
    }
    return render(request, "payroll/dashboard.html", context=context)


@login_required
def dashboard_employee_chart(request):
    """
    payroll dashboard employee chart data
    """

    date = request.GET.get("period")
    year = date.split("-")[0]
    month = date.split("-")[1]
    dataset = []

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if is_ajax and request.method == "GET":
        employee_list = Payslip.objects.filter(
            Q(start_date__month=month) & Q(start_date__year=year)
        )
        labels = []
        for employee in employee_list:
            labels.append(employee.employee_id)

        colors = [
            "rgba(255, 99, 132, 1)",  # Red
            "rgba(255, 206, 86, 1)",  # Yellow
            "rgba(54, 162, 235, 1)",  # Blue
            "rgba(75, 242, 182, 1)",  # green
        ]

        for choice, color in zip(Payslip.status_choices, colors):
            dataset.append(
                {
                    "label": choice[0],
                    "data": [],
                    "backgroundColor": color,
                }
            )

        employees = [employee.employee_id for employee in employee_list]

        employees = list(set(employees))
        total_pay_with_status = defaultdict(lambda: defaultdict(float))

        for label in employees:
            payslips = employee_list.filter(employee_id=label)
            for payslip in payslips:
                total_pay_with_status[payslip.status][label] += round(
                    payslip.net_pay, 2
                )

        for data in dataset:
            dataset_label = data["label"]
            data["data"] = [
                total_pay_with_status[dataset_label][label] for label in employees
            ]

        employee_label = []
        for employee in employees:
            employee_label.append(
                f"{employee.employee_first_name} {employee.employee_last_name}"
            )

        for value, choice in zip(dataset, Payslip.status_choices):
            if value["label"] == choice[0]:
                value["label"] = choice[1]

        list_of_employees = list(
            Employee.objects.values_list(
                "id", "employee_first_name", "employee_last_name"
            )
        )
        response = {
            "dataset": dataset,
            "labels": employee_label,
            "employees": list_of_employees,
            "message": _("No payslips generated for this month."),
        }
        return JsonResponse(response)


def payslip_details(request):
    """
    payroll dashboard payslip details data
    """

    date = request.GET.get("period")
    year = date.split("-")[0]
    month = date.split("-")[1]
    employee_list = []
    employee_list = Payslip.objects.filter(
        Q(start_date__month=month) & Q(start_date__year=year)
    )
    total_amount = 0
    for employee in employee_list:
        total_amount += employee.net_pay

    response = {
        "no_of_emp": len(employee_list),
        "total_amount": round(total_amount, 2),
    }
    return JsonResponse(response)


@login_required
def dashboard_department_chart(request):
    """
    payroll dashboard department chart data
    """

    date = request.GET.get("period")
    year = date.split("-")[0]
    month = date.split("-")[1]
    dataset = [
        {
            "label": "",
            "data": [],
            "backgroundColor": ["#8de5b3", "#f0a8a6", "#8ed1f7", "#f8e08e", "#c2c7cc"],
        }
    ]
    department = []
    department_total = []

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if is_ajax and request.method == "GET":
        employee_list = Payslip.objects.filter(
            Q(start_date__month=month) & Q(start_date__year=year)
        )

        for employee in employee_list:
            department.append(
                employee.employee_id.employee_work_info.department_id.department
            )

        department = list(set(department))
        for depart in department:
            department_total.append({"department": depart, "amount": 0})

        for employee in employee_list:
            employee_department = (
                employee.employee_id.employee_work_info.department_id.department
            )

            for depart in department_total:
                if depart["department"] == employee_department:
                    depart["amount"] += round(employee.net_pay, 2)

        colors = generate_colors(len(department))

        dataset = [
            {
                "label": "",
                "data": [],
                "backgroundColor": colors,
            }
        ]

        for depart_total, depart in zip(department_total, department):
            if depart == depart_total["department"]:
                dataset[0]["data"].append(depart_total["amount"])

        response = {
            "dataset": dataset,
            "labels": department,
            "department_total": department_total,
            "message": _("No payslips generated for this month."),
        }
        return JsonResponse(response)


def contract_ending(request):
    """
    payroll dashboard contract ending details data
    """

    date = request.GET.get("period")
    month = date.split("-")[1]
    year = date.split("-")[0]

    if request.GET.get("initialLoad") == "true":
        if month == "12":
            month = 0
            year = int(year) + 1

        contract_end = Contract.objects.filter(
            contract_end_date__month=int(month) + 1, contract_end_date__year=int(year)
        )
    else:
        contract_end = Contract.objects.filter(
            contract_end_date__month=int(month), contract_end_date__year=int(year)
        )

    ending_contract = []
    for contract in contract_end:
        ending_contract.append(
            {"contract_name": contract.contract_name, "contract_id": contract.id}
        )

    response = {
        "contract_end": ending_contract,
        "message": _("No contracts ending this month"),
    }
    return JsonResponse(response)


def payslip_export(request):
    """
    payroll dashboard exporting to excell data

    Args:
    - request (HttpRequest): The HTTP request object.
    - contract_id (int): The ID of the contract to view.

    """

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    employee = request.GET.get("employee")
    status = request.GET.get("status")
    department = []
    total_amount = 0

    table1_data = []
    table2_data = []
    table3_data = []
    table4_data = []

    employee_payslip_list = Payslip.objects.all()

    if start_date:
        employee_payslip_list = employee_payslip_list.filter(start_date__gte=start_date)

    if end_date:
        employee_payslip_list = employee_payslip_list.filter(end_date__lte=end_date)

    if employee:
        employee_payslip_list = employee_payslip_list.filter(employee_id=employee)

    if status:
        employee_payslip_list = employee_payslip_list.filter(status=status)
    user = request.user
    emp = user.employee_get
    for payslip in employee_payslip_list:
        # Taking the company_name of the user
        info = EmployeeWorkInformation.objects.filter(employee_id=emp)
        if info.exists():
            for data in info:
                employee_company = data.company_id
            company_name = Company.objects.filter(company=employee_company)
            emp_company = company_name.first()

            # Access the date_format attribute directly
            date_format = emp_company.date_format if emp_company else "MMM. D, YYYY"
        else:
            date_format = "MMM. D, YYYY"
        # Define date formats
        date_formats = {
            "DD-MM-YYYY": "%d-%m-%Y",
            "DD.MM.YYYY": "%d.%m.%Y",
            "DD/MM/YYYY": "%d/%m/%Y",
            "MM/DD/YYYY": "%m/%d/%Y",
            "YYYY-MM-DD": "%Y-%m-%d",
            "YYYY/MM/DD": "%Y/%m/%d",
            "MMMM D, YYYY": "%B %d, %Y",
            "DD MMMM, YYYY": "%d %B, %Y",
            "MMM. D, YYYY": "%b. %d, %Y",
            "D MMM. YYYY": "%d %b. %Y",
            "dddd, MMMM D, YYYY": "%A, %B %d, %Y",
        }
        start_date_str = str(payslip.start_date)
        end_date_str = str(payslip.end_date)

        # Convert the string to a datetime.date object
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # Print the formatted date for each format
        for format_name, format_string in date_formats.items():
            if format_name == date_format:
                formatted_start_date = start_date.strftime(format_string)

        for format_name, format_string in date_formats.items():
            if format_name == date_format:
                formatted_end_date = end_date.strftime(format_string)

        table1_data.append(
            {
                "employee": payslip.employee_id.employee_first_name
                + " "
                + payslip.employee_id.employee_last_name,
                "start_date": formatted_start_date,
                "end_date": formatted_end_date,
                "basic_pay": round(payslip.basic_pay, 2),
                "deduction": round(payslip.deduction, 2),
                "allowance": round(payslip.gross_pay - payslip.basic_pay, 2),
                "gross_pay": round(payslip.gross_pay, 2),
                "net_pay": round(payslip.net_pay, 2),
                "status": status_choices.get(payslip.status),
            },
        )

    if not employee_payslip_list:
        table1_data.append(
            {
                "employee": "None",
                "start_date": "None",
                "end_date": "None",
                "basic_pay": "None",
                "deduction": "None",
                "allowance": "None",
                "gross_pay": "None",
                "net_pay": "None",
                "status": "None",
            },
        )

    for employee in employee_payslip_list:
        department.append(
            employee.employee_id.employee_work_info.department_id.department
        )

    department = list(set(department))

    for depart in department:
        table2_data.append({"Department": depart, "Amount": 0})

    for employee in employee_payslip_list:
        employee_department = (
            employee.employee_id.employee_work_info.department_id.department
        )

        for depart in table2_data:
            if depart["Department"] == employee_department:
                depart["Amount"] += round(employee.net_pay, 2)

    if not employee_payslip_list:
        table2_data.append({"Department": "None", "Amount": 0})

    contract_end = Contract.objects.all()
    if not start_date and not end_date:
        contract_end = contract_end.filter(
            Q(contract_end_date__month=datetime.now().month)
            & Q(contract_end_date__year=datetime.now().year)
        )
    if end_date:
        contract_end = contract_end.filter(contract_end_date__lte=end_date)

    if start_date:
        if not end_date:
            contract_end = contract_end.filter(
                Q(contract_end_date__gte=start_date)
                & Q(contract_end_date__lte=datetime.now())
            )
        else:
            contract_end = contract_end.filter(contract_end_date__gte=start_date)

    table3_data = {"contract_ending": []}

    for contract in contract_end:
        table3_data["contract_ending"].append(contract.contract_name)

    if not contract_end:
        table3_data["contract_ending"].append("None")

    for employee in employee_payslip_list:
        total_amount += round(employee.net_pay, 2)

    table4_data = {
        "no_of_payslip_generated": len(employee_payslip_list),
        "total_amount": [total_amount],
    }

    df_table1 = pd.DataFrame(table1_data)
    df_table2 = pd.DataFrame(table2_data)
    df_table3 = pd.DataFrame(table3_data)
    df_table4 = pd.DataFrame(table4_data)

    df_table1 = df_table1.rename(
        columns={
            "employee": "Employee",
            "start_date": "Start Date",
            "end_date": "End Date",
            "deduction": "Deduction",
            "allowance": "Allowance",
            "gross_pay": "Gross Pay",
            "net_pay": "Net Pay",
            "status": "Status",
        }
    )

    df_table3 = df_table3.rename(
        columns={
            "contract_ending": (
                f"Contract Ending {start_date} to {end_date}"
                if start_date and end_date
                else f"Contract Ending"
            ),
        }
    )

    df_table4 = df_table4.rename(
        columns={
            "no_of_payslip_generated": "Number of payslips generated",
            "total_amount": "Total Amount",
        }
    )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=payslip.xlsx"

    writer = pd.ExcelWriter(response, engine="xlsxwriter")
    df_table1.to_excel(
        writer, sheet_name="Payroll Dashboard details", index=False, startrow=3
    )
    df_table2.to_excel(
        writer,
        sheet_name="Payroll Dashboard details",
        index=False,
        startrow=len(df_table1) + 3 + 3,
    )
    df_table3.to_excel(
        writer,
        sheet_name="Payroll Dashboard details",
        index=False,
        startrow=len(df_table1) + 3 + len(df_table2) + 6,
    )
    df_table4.to_excel(
        writer,
        sheet_name="Payroll Dashboard details",
        index=False,
        startrow=len(df_table1) + 3 + len(df_table2) + len(df_table3) + 9,
    )

    workbook = writer.book
    worksheet = writer.sheets["Payroll Dashboard details"]
    max_columns = max(
        len(df_table1.columns),
        len(df_table2.columns),
        len(df_table3.columns),
        len(df_table4.columns),
    )

    heading_format = workbook.add_format(
        {
            "bold": True,
            "font_size": 14,
            "align": "center",
            "valign": "vcenter",
            "bg_color": "#B2ED67",
            "font_size": 20,
        }
    )

    worksheet.set_row(0, 30)
    worksheet.merge_range(
        0,
        0,
        0,
        max_columns - 1,
        (
            f"Payroll details {start_date} to {end_date}"
            if start_date and end_date
            else f"Payroll details"
        ),
        heading_format,
    )

    header_format = workbook.add_format(
        {"bg_color": "#B2ED67", "bold": True, "text_wrap": True}
    )

    for col_num, value in enumerate(df_table1.columns.values):
        worksheet.write(3, col_num, value, header_format)
        col_letter = chr(65 + col_num)

        header_width = max(len(value) + 2, len(df_table1[value].astype(str).max()) + 2)
        worksheet.set_column(f"{col_letter}:{col_letter}", header_width)

    for col_num, value in enumerate(df_table2.columns.values):
        worksheet.write(len(df_table1) + 3 + 3, col_num, value, header_format)
        col_letter = chr(65 + col_num)

        header_width = max(len(value) + 2, len(df_table2[value].astype(str).max()) + 2)
        worksheet.set_column(f"{col_letter}:{col_letter}", header_width)

    for col_num, value in enumerate(df_table3.columns.values):
        worksheet.write(
            len(df_table1) + 3 + len(df_table2) + 6, col_num, value, header_format
        )
        col_letter = chr(65 + col_num)

        header_width = max(len(value) + 2, len(df_table3[value].astype(str).max()) + 2)
        worksheet.set_column(f"{col_letter}:{col_letter}", header_width)

    for col_num, value in enumerate(df_table4.columns.values):
        worksheet.write(
            len(df_table1) + 3 + len(df_table2) + len(df_table3) + 9,
            col_num,
            value,
            header_format,
        )
        col_letter = chr(65 + col_num)

        header_width = max(len(value) + 2, len(df_table4[value].astype(str).max()) + 2)
        worksheet.set_column(f"{col_letter}:{col_letter}", header_width)

    worksheet.set_row(len(df_table1) + len(df_table2) + 9, 30)

    writer.close()

    return response


@login_required
@permission_required("payroll.delete_payslip")
def payslip_bulk_delete(request):
    """
    This method is used to bulk delete for Payslip
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for id in ids:
        try:
            payslip = Payslip.objects.get(id=id)
            period = f"{payslip.start_date} to {payslip.end_date}"
            payslip.delete()
            messages.success(
                request,
                _("{employee} {period} payslip deleted.").format(
                    employee=payslip.employee_id, period=period
                ),
            )
        except Payslip.DoesNotExist:
            messages.error(request, _("Payslip not found."))
        except ProtectedError:
            messages.error(
                request,
                _("You cannot delete {payslip}").format(payslip=payslip),
            )
    return JsonResponse({"message": "Success"})


@login_required
@permission_required("payroll.change_payslip")
def slip_group_name_update(request):
    """
    This method is used to update the group of the payslip
    """
    new_name = request.POST["newName"]
    group_name = request.POST["previousName"]
    Payslip.objects.filter(group_name=group_name).update(group_name=new_name)
    return JsonResponse(
        {"type": "success", "message": "Batch name updated.", "new_name": new_name}
    )


@login_required
@permission_required("payroll.add_contract")
def contract_export(request):
    return export_data(
        request=request,
        model=Contract,
        filter_class=ContractFilter,
        form_class=ContractExportFieldForm,
        file_name="Contract_export",
    )


@login_required
@permission_required("payroll.delete_contract")
def contract_bulk_delete(request):
    """
    This method is used to bulk delete Contract
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for id in ids:
        try:
            contract = Contract.objects.get(id=id)
            name = f"{contract.contract_name}"
            contract.delete()
            messages.success(
                request,
                _("{name} deleted.").format(name=name),
            )
        except Payslip.DoesNotExist:
            messages.error(request, _("Contract not found."))
        except ProtectedError:
            messages.error(
                request,
                _("You cannot delete {contract}").format(contract=contract),
            )
    return JsonResponse({"message": "Success"})


def payslip_pdf(request, id):
    if (
        request.user.has_perm("payroll.view_payslip")
        or payslip.employee_id.employee_user_id == request.user
    ):
        user = request.user
        employee = user.employee_get

        # Taking the company_name of the user
        info = EmployeeWorkInformation.objects.filter(employee_id=employee)
        if info.exists():
            for data in info:
                employee_company = data.company_id
            company_name = Company.objects.filter(company=employee_company)
            emp_company = company_name.first()

            # Access the date_format attribute directly
            date_format = (
                emp_company.date_format
                if emp_company and emp_company.date_format
                else "MMM. D, YYYY"
            )
        else:
            date_format = "MMM. D, YYYY"

        payslip = Payslip.objects.get(id=id)
        data = payslip.pay_head_data
        start_date_str = data["start_date"]
        end_date_str = data["end_date"]

        # Define date formats
        date_formats = {
            "DD-MM-YYYY": "%d-%m-%Y",
            "DD.MM.YYYY": "%d.%m.%Y",
            "DD/MM/YYYY": "%d/%m/%Y",
            "MM/DD/YYYY": "%m/%d/%Y",
            "YYYY-MM-DD": "%Y-%m-%d",
            "YYYY/MM/DD": "%Y/%m/%d",
            "MMMM D, YYYY": "%B %d, %Y",
            "DD MMMM, YYYY": "%d %B, %Y",
            "MMM. D, YYYY": "%b. %d, %Y",
            "D MMM. YYYY": "%d %b. %Y",
            "dddd, MMMM D, YYYY": "%A, %B %d, %Y",
        }

        # Convert the string to a datetime.date object
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # Print the formatted date for each format
        for format_name, format_string in date_formats.items():
            if format_name == date_format:
                formatted_start_date = start_date.strftime(format_string)

        for format_name, format_string in date_formats.items():
            if format_name == date_format:
                formatted_end_date = end_date.strftime(format_string)

        data["formatted_start_date"] = formatted_start_date
        data["formatted_end_date"] = formatted_end_date
        data["employee"] = payslip.employee_id
        data["payslip"] = payslip
        data["json_data"] = data.copy()
        data["json_data"]["employee"] = payslip.employee_id.id
        data["json_data"]["payslip"] = payslip.id
        data["instance"] = payslip
        data["currency"] = PayrollSettings.objects.first().currency_symbol

    return generate_pdf("payroll/payslip/individual_pdf.html", context=data)


@login_required
@permission_required("payroll.view_contract")
def contract_select(request):
    page_number = request.GET.get("page")

    if page_number == "all":
        employees = Contract.objects.all()

    contract_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"contract_ids": contract_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def contract_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        contract_filter = ContractFilter(filters, queryset=Contract.objects.all())

        # Get the filtered queryset
        filtered_employees = contract_filter.qs

        contract_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"contract_ids": contract_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
def payslip_select(request):
    page_number = request.GET.get("page")

    if page_number == "all":
        if request.user.has_perm("payroll.view_payslip"):
            employees = Payslip.objects.all()
        else:
            employees = Payslip.objects.filter(
                employee_id__employee_user_id=request.user
            )

    payslip_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"payslip_ids": payslip_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def payslip_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        payslip_filter = PayslipFilter(filters, queryset=Payslip.objects.all())

        # Get the filtered queryset
        filtered_employees = payslip_filter.qs

        payslip_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"payslip_ids": payslip_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
def create_payrollrequest_comment(request, payroll_id):
    """
    This method renders form and template to create Reimbursement request comments
    """
    payroll = Reimbursement.objects.filter(id=payroll_id).first()
    emp = request.user.employee_get
    form = ReimbursementrequestCommentForm(
        initial={"employee_id": emp.id, "request_id": payroll_id}
    )

    if request.method == "POST":
        form = ReimbursementrequestCommentForm(request.POST)
        if form.is_valid():
            form.instance.employee_id = emp
            form.instance.request_id = payroll
            form.save()
            form = ReimbursementrequestCommentForm(
                initial={"employee_id": emp.id, "request_id": payroll_id}
            )
            messages.success(request, _("Comment added successfully!"))

            if request.user.employee_get.id == payroll.employee_id.id:
                rec = (
                    payroll.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                )
                notify.send(
                    request.user.employee_get,
                    recipient=rec,
                    verb=f"{payroll.employee_id}'s reimbursement request has received a comment.",
                    verb_ar=f"تلقى طلب استرداد نفقات {payroll.employee_id} تعليقًا.",
                    verb_de=f"{payroll.employee_id}s Rückerstattungsantrag hat einen Kommentar erhalten.",
                    verb_es=f"La solicitud de reembolso de gastos de {payroll.employee_id} ha recibido un comentario.",
                    verb_fr=f"La demande de remboursement de frais de {payroll.employee_id} a reçu un commentaire.",
                    redirect="/payroll/view-reimbursement",
                    icon="chatbox-ellipses",
                )
            elif (
                request.user.employee_get.id
                == payroll.employee_id.employee_work_info.reporting_manager_id.id
            ):
                rec = payroll.employee_id.employee_user_id
                notify.send(
                    request.user.employee_get,
                    recipient=rec,
                    verb="Your reimbursement request has received a comment.",
                    verb_ar="تلقى طلب استرداد نفقاتك تعليقًا.",
                    verb_de="Ihr Rückerstattungsantrag hat einen Kommentar erhalten.",
                    verb_es="Tu solicitud de reembolso ha recibido un comentario.",
                    verb_fr="Votre demande de remboursement a reçu un commentaire.",
                    redirect="/payroll/view-reimbursement",
                    icon="chatbox-ellipses",
                )
            else:
                rec = [
                    payroll.employee_id.employee_user_id,
                    payroll.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                ]
                notify.send(
                    request.user.employee_get,
                    recipient=rec,
                    verb=f"{payroll.employee_id}'s reimbursement request has received a comment.",
                    verb_ar=f"تلقى طلب استرداد نفقات {payroll.employee_id} تعليقًا.",
                    verb_de=f"{payroll.employee_id}s Rückerstattungsantrag hat einen Kommentar erhalten.",
                    verb_es=f"La solicitud de reembolso de gastos de {payroll.employee_id} ha recibido un comentario.",
                    verb_fr=f"La demande de remboursement de frais de {payroll.employee_id} a reçu un commentaire.",
                    redirect="/payroll/view-reimbursement",
                    icon="chatbox-ellipses",
                )

            return HttpResponse("<script>window.location.reload()</script>")
    return render(
        request,
        "payroll/reimbursement/reimbursement_request_comment_form.html",
        {"form": form, "request_id": payroll_id},
    )


@login_required
def view_payrollrequest_comment(request, payroll_id):
    """
    This method is used to show Reimbursement request comments
    """
    comments = ReimbursementrequestComment.objects.filter(
        request_id=payroll_id
    ).order_by("-created_at")
    no_comments = False
    if not comments.exists():
        no_comments = True

    return render(
        request,
        "payroll/reimbursement/comment_view.html",
        {"comments": comments, "no_comments": no_comments},
    )


@login_required
def delete_payrollrequest_comment(request, comment_id):
    """
    This method is used to delete Reimbursement request comments
    """
    ReimbursementrequestComment.objects.get(id=comment_id).delete()

    messages.success(request, _("Comment deleted successfully!"))
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@permission_required("payroll.add_payrollgeneralsetting")
def initial_notice_period(request):
    """
    This method is used to set initial value notice period
    """
    notice_period = eval(request.GET["notice_period"])
    settings = PayrollGeneralSetting.objects.first()
    settings = settings if settings else PayrollGeneralSetting()
    settings.notice_period = max(notice_period, 0)
    settings.save()
    messages.success(request, "Initial notice period updated")
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
