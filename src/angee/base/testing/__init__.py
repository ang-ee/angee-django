"""Permission-neutral addon metadata and test support.

Native pytest-django owns database isolation. Tests using the optional IAM addon
select its permission fixture plugin explicitly.
"""
