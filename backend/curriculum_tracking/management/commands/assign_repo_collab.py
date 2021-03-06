from django.core.management.base import BaseCommand
from core.models import Cohort, RecruitCohort, UserGroup
from curriculum_tracking.models import ContentItem, RecruitProject, AgileCard
from git_real.constants import PERSONAL_GITHUB_NAME, ORGANISATION

from git_real.helpers import add_collaborator
from social_auth.github_api import Api
import re
import random
from django.contrib.auth import get_user_model
from social_auth.models import SocialProfile

User = get_user_model()

COHORT_SELF_REVIEW = "COHORT_SELF_REVIEW"
COHORT_REVIEW_OTHER = "COHORT_REVIEW_OTHER"
GROUP_SELF_REVIEW = "GROUP_SELF_REVIEW"
GROUP_REVIEW_OTHER = "GROUP_REVIEW_OTHER"
GIT_USER_REPO_ONLY = "GIT_USER_REPO_ONLY"
GIT_USER_AS_REVIEWER = "GIT_USER_AS_REVIEWER"


def get_user_projects(users, content_item):
    projects = set()
    # TODO: ISSUE make this function more efficient
    for user in users:
        for proj in RecruitProject.objects.filter(
            content_item=content_item, recruit_users__in=[user]
        ):
            projects.add(proj)

    return projects


def get_projects(cohort, content_item):
    users = [o.user for o in RecruitCohort.objects.filter(cohort=cohort)]
    users = [o for o in users if o.active]
    return get_user_projects(users, content_item)


def has_social_profile(user):
    try:
        user.social_profile
    except SocialProfile.DoesNotExist:
        return False
    return True


def shuffle_project_reviewers(projects, users):
    projects = list(projects)
    users = [o for o in users if has_social_profile(o) and o.active]
    random.shuffle(users)
    while len(users) < len(projects):
        users.extend(users)

    for (project, user) in zip(projects, users):
        if (user in project.recruit_users.all()) or (
            user in project.reviewer_users.all()
        ):
            return shuffle_project_reviewers(projects, users)
    # we have a winner
    return zip(projects, users)


def cohort_review_other(cohort_name, content_item, reviewer):
    cohort = Cohort.get_from_short_name(cohort_name)
    projects = get_projects(cohort, content_item)
    reviewer_cohort = Cohort.get_from_short_name(reviewer)
    reviewer_users = [o for o in reviewer_cohort.get_member_users() if o.active]
    assign_random_reviewers(projects, reviewer_users)


def get_group(group_name):
    return UserGroup.objects.get(name=group_name)


def get_group_projects(group, content_item):
    return get_user_projects(group.active_student_users, content_item)


def group_self_review(group_name, content_item, reviewer=None):
    if reviewer:
        raise Exception(
            "Unexpected reviewer argument. When shuffling a cohort then dont supply a reviewer"
        )
    group = get_group(group_name)
    projects = get_group_projects(group, content_item)
    users = group.active_student_users
    assign_random_reviewers(projects, users)


def group_review_other(group_name, content_item, reviewer):
    group = get_group(group_name)
    projects = get_group_projects(group, content_item)
    reviewer_group = get_group(reviewer)
    reviewer_users = reviewer_group.active_student_users
    assign_random_reviewers(projects, reviewer_users)


def cohort_self_review(cohort_name, content_item, reviewer=None):
    if reviewer:
        raise Exception(
            "Unexpected reviewer argument. When shuffling a cohort then dont supply a reviewer"
        )
    cohort = Cohort.get_from_short_name(cohort_name)
    projects = get_projects(cohort, content_item)
    users = [o.user for o in RecruitCohort.objects.filter(cohort=cohort)]
    users = [o for o in users if o.active]
    assign_random_reviewers(projects, users)


def assign_random_reviewers(projects, users):
    api = Api(PERSONAL_GITHUB_NAME)
    shuffled_reviewers = list(
        shuffle_project_reviewers(projects, [o for o in users if o.active])
    )
    broken = [
        project
        for project, _ in shuffled_reviewers
        if not project.repository.full_name.startswith(ORGANISATION)
    ]
    # assert broken == [], "\n".join([f"{project.id} {project}" for project in broken])

    for project, user in shuffled_reviewers:
        print(user)
        if project.repository and project.repository.full_name.startswith(ORGANISATION):
            add_collaborator(
                api, project.repository.full_name, user.social_profile.github_name
            )
        project.reviewer_users.add(user)
        project.save()
        try:
            card = project.agile_card
        except AgileCard.DoesNotExist:
            pass
        else:
            assert card is not None
            card.reviewers.set(project.reviewer_users.all())
            card.save()


def add_reviewer(cohort, content_item, reviewer, add_as_project_reviewer):
    api = Api(PERSONAL_GITHUB_NAME)

    projects = get_projects(cohort, content_item)
    # for o in projects: print(f"{o.id} {o}\n\t{o.content_item}\n\t{o.repository.full_name}\n")
    # cohort_users = cohort.get_member_users()
    # assert len(projects) == len(cohort_users), f"{projects}\n{cohort_users}"
    # if add_as_project_reviewer:
    if "@" in reviewer:
        user = User.objects.get(email=reviewer)
    else:
        user = User.objects.get(social_profile__github_name=reviewer)

    github_name = user.social_profile.github_name
    # else:
    #     github_name = reviewer

    for project in projects:
        print(project)
        if project.repository:
            add_collaborator(api, project.repository.full_name, github_name)
        project.save()
        if add_as_project_reviewer:
            project.reviewer_users.add(user)
        project.save()
        # project.agile_card.reviewers =


def git_user_as_reviewer(cohort_name, content_item, reviewer):
    cohort = Cohort.get_from_short_name(cohort_name)
    add_reviewer(cohort, content_item, reviewer, add_as_project_reviewer=True)


def git_user_repo_only(cohort_name, content_item, reviewer):
    cohort = Cohort.get_from_short_name(cohort_name)
    add_reviewer(cohort, content_item, reviewer, add_as_project_reviewer=False)


allowed_commands = {
    COHORT_SELF_REVIEW: cohort_self_review,
    COHORT_REVIEW_OTHER: cohort_review_other,
    GIT_USER_AS_REVIEWER: git_user_as_reviewer,
    GIT_USER_REPO_ONLY: git_user_repo_only,
    GROUP_SELF_REVIEW: group_self_review,
    GROUP_REVIEW_OTHER: group_review_other,
}


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("command", type=str)
        parser.add_argument("cohort", type=str)
        parser.add_argument("content_item", type=str)
        parser.add_argument("reviewer", type=str, nargs="?")

    def handle(self, *args, **options):
        command = options["command"]
        assert (
            command in allowed_commands
        ), f"command '{command}' not allowed. Choose one of {list(allowed_commands.keys())}"
        cohort_name = options["cohort"]
        content_item_name = options["content_item"]
        reviewer = options["reviewer"]

        content_item = ContentItem.objects.get(
            title=content_item_name, content_type=ContentItem.PROJECT
        )

        allowed_commands[command](
            cohort_name, content_item=content_item, reviewer=reviewer
        )


"""
python manage.py assign_repo_collab GIT_USER_REPO_ONLY "C20 java" "Introduction to Spring Boot - part 3" RuddyN
python manage.py assign_repo_collab GIT_USER_REPO_ONLY "C20 java" "Introduction to Spring Boot - part 3" elijah.sepuru@umuzi.org
python manage.py assign_repo_collab GIT_USER_REPO_ONLY "C20 java" "Introduction to Spring Boot - part 3"  dibwe.kalangu@umuzi.org
python manage.py assign_repo_collab COHORT_SELF_REVIEW "C20 java" "Introduction to Spring Boot - part 3"



python manage.py assign_repo_collab COHORT_SELF_REVIEW "C20 data eng" "RabbitMQ"
python manage.py assign_repo_collab GIT_USER_REPO_ONLY "C20 data eng" "RabbitMQ" owen.mafane@umuzi.org



python manage.py assign_repo_collab COHORT_SELF_REVIEW "C20 java" "Java data structures"


python manage.py assign_repo_collab COHORT_REVIEW_OTHER "C22 web dev no nqf" "Level 1 programming katas" "C21 web dev"

python manage.py assign_repo_collab GIT_USER_REPO_ONLY "C22 web dev no nqf" "Level 1 programming katas" "ng.codeclub@gmail.com"

"""
