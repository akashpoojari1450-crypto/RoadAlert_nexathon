from rest_framework.permissions import BasePermission

from .models import User


class IsEmployer(BasePermission):
    message = "Employer account required."

    def has_permission(self, request, view):
        u = request.user
        return bool(u.is_authenticated and u.role in (User.Role.EMPLOYER, User.Role.ADMIN) and u.company_id)


class IsAuthorityOrAdmin(BasePermission):
    message = "Traffic authority or administrator account required."

    def has_permission(self, request, view):
        u = request.user
        return bool(u.is_authenticated and u.role in (User.Role.AUTHORITY, User.Role.ADMIN))
