import frappe
from frappe.model.document import Document

from buyback.utils import next_numeric_external_id


class GradeMaster(Document):

    def before_insert(self):
        self.grade_id = next_numeric_external_id("Grade Master", "grade_id")
