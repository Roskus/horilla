"""
search.py

This module is used to register search/filter views methods
"""

import json
from urllib.parse import parse_qs
from django.shortcuts import render
from django.core.paginator import Paginator
from django.contrib.auth.models import User
from horilla.decorators import login_required, permission_required
from base.methods import sortby, get_key_instances
from recruitment.filters import (
    CandidateFilter,
    RecruitmentFilter,
    StageFilter,
    SurveyFilter,
    SurveyTemplateFilter,
)
from recruitment.models import (
    Candidate,
    Recruitment,
    Stage,
    RecruitmentSurvey,
    SurveyTemplate,
)
from attendance.methods.group_by import group_by_queryset
from recruitment.views.paginator_qry import paginator_qry


@login_required
@permission_required(perm="recruitment.view_recruitment")
def recruitment_search(request):
    """
    This method is used to search recruitment
    """
    filter_obj = RecruitmentFilter(request.GET)
    previous_data = request.GET.urlencode()
    recruitment_obj = sortby(request, filter_obj.qs, "orderby")
    data_dict = parse_qs(previous_data)
    if not request.GET.get("is_active"):
        filter_obj.form.initial["is_active"] = True
        data_dict["is_active"] = request.GET.get("is_active")
        recruitment_obj = recruitment_obj.filter(is_active=True)

        for key, val in data_dict.copy().items():
            try:
                if val[0] != "False" or key == "view":
                    del data_dict[key]
            except:
                del data_dict[key]

    get_key_instances(Recruitment, data_dict)

    return render(
        request,
        "recruitment/recruitment_component.html",
        {
            "data": paginator_qry(recruitment_obj, request.GET.get("page")),
            "pd": previous_data,
            "filter_dict": data_dict,
        },
    )


@login_required
@permission_required(perm="recruitment.view_stage")
def stage_search(request):
    """
    This method is used to search stage
    """

    stages = StageFilter(request.GET).qs
    previous_data = request.GET.urlencode()
    stages = sortby(request, stages, "orderby")
    data_dict = parse_qs(previous_data)
    get_key_instances(Stage, data_dict)
    recruitments = group_by_queryset(
        stages, "recruitment_id", request.GET.get("rpage"), "rpage"
    )

    return render(
        request,
        "stage/stage_group.html",
        {
            "data": paginator_qry(stages, request.GET.get("page")),
            "recruitments": recruitments,
            "pd": previous_data,
            "filter_dict": data_dict,
        },
    )


@login_required
@permission_required(perm="recruitment.view_candidate")
def candidate_search(request):
    """
    This method is used to search candidate model and return matching objects
    """
    previous_data = request.GET.urlencode()
    search = request.GET.get("search")
    if search is None:
        search = ""
    candidates = Candidate.objects.filter(name__icontains=search)
    candidates = CandidateFilter(request.GET, queryset=candidates).qs
    data_dict = []
    if not request.GET.get("dashboard"):
        data_dict = parse_qs(previous_data)
        get_key_instances(Candidate, data_dict)

    template = "candidate/candidate_card.html"
    if request.GET.get("view") == "list":
        template = "candidate/candidate_list.html"
    candidates = sortby(request, candidates, "orderby")

    field = request.GET.get("field")
    if field != "" and field is not None:
        field_copy = field.replace(".", "__")
        candidates = candidates.order_by(field_copy)
        template = "candidate/group_by.html"

    candidates = paginator_qry(candidates, request.GET.get("page"))

    mails = list(Candidate.objects.values_list("email", flat=True))
    # Query the User model to check if any email is present
    existing_emails = list(
        User.objects.filter(username__in=mails).values_list("email", flat=True)
    )

    return render(
        request,
        template,
        {
            "data": candidates,
            "pd": previous_data,
            "filter_dict": data_dict,
            "field": field,
            "emp_list": existing_emails,
        },
    )


@login_required
@permission_required(perm="recruitment.view_candidate")
def pipeline_candidate_search(request):
    """
    This method is used to search  candidate
    """
    template = "pipeline/pipeline_components/kanban_tabs.html"
    if request.GET.get("view") == "card":
        template = "pipeline/pipeline_components/kanban_tabs.html"
    return render(request, template)


@login_required
@permission_required(perm="recruitment.view_candidate")
def candidate_filter_view(request):
    """
    This method is used for filter,pagination and search candidate.
    """
    candidates = Candidate.objects.filter(is_active=True)
    template = "candidate/candidate_card.html"
    if request.GET.get("view") == "list":
        template = "candidate/candidate_list.html"

    previous_data = request.GET.urlencode()
    filter_obj = CandidateFilter(request.GET, queryset=candidates)
    paginator = Paginator(filter_obj.qs, 24)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        template,
        {"data": page_obj, "pd": previous_data},
    )


@login_required
@permission_required(perm="recruitment.view_recruitmentsurvey")
def filter_survey(request):
    """
    This method is used to filter/search the recruitment surveys
    """
    previous_data = request.GET.urlencode()
    filter_obj = SurveyFilter(request.GET)
    questions = filter_obj.qs
    templates = group_by_queryset(
        questions.filter(template_id__isnull=False).distinct(),
        "template_id__title",
        page=request.GET.get("template_page"),
        page_name="template_page",
        records_per_page=50,
    )
    all_template_object_list = []
    for template in templates:
        all_template_object_list.append(template)

    survey_templates = SurveyTemplateFilter(request.GET).qs

    all_templates = survey_templates.values_list("title", flat=True)
    used_templates = questions.values_list("template_id__title", flat=True)

    unused_templates = list(set(all_templates) - set(used_templates))
    unused_groups = []
    for template_name in unused_templates:
        unused_groups.append(
            {
                "grouper": template_name,
                "list": [],
                "dynamic_name": "",
            }
        )
    all_template_object_list = all_template_object_list + unused_groups
    templates = paginator_qry(
        all_template_object_list, request.GET.get("template_page")
    )
    survey_templates = paginator_qry(
        survey_templates, request.GET.get("survey_template_page")
    )
    requests_ids = json.dumps(
        [
            instance.id
            for instance in paginator_qry(
                questions, request.GET.get("page")
            ).object_list
        ]
    )
    data_dict = parse_qs(previous_data)
    get_key_instances(RecruitmentSurvey, data_dict)
    return render(
        request,
        "survey/survey_card.html",
        {
            "questions": paginator_qry(questions, request.GET.get("page")),
            "templates": templates,
            "survey_templates": survey_templates,
            "pd": previous_data,
            "filter_dict": data_dict,
            "requests_ids": requests_ids,
        },
    )
