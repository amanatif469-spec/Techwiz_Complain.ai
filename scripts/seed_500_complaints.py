import csv
import os
import sys
from datetime import datetime, timezone

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database.session import session_scope
from database.models import Complaint, Customer, CustomerType, Channel, ComplaintStatus, User
from complaint_processing.preprocess import content_hash, sanitize_input
from python_validation.pipeline import run_python_validation
from complaint_processing.sla import apply_sla

def seed_complaints():
    csv_path = os.path.join(BASE_DIR, "sample_complaints", "nimbuscarta_500.csv")
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        return

    with session_scope() as db:
        # Get or create standard and vip customers
        vip_cust = db.query(Customer).filter(Customer.customer_type == CustomerType.vip).first()
        std_cust = db.query(Customer).filter(Customer.customer_type == CustomerType.standard).first()
        admin_user = db.query(User).first()

        existing_hashes = {c.content_hash for c in db.query(Complaint.content_hash).all()}
        existing_count = db.query(Complaint).count()
        print(f"Current DB complaints: {existing_count}")

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            added = 0
            for idx, row in enumerate(reader, start=existing_count + 1):
                desc = row.get("description", "").strip()
                title = row.get("title", "").strip()
                chash = content_hash(desc)

                if chash in existing_hashes:
                    continue

                cust_type_str = row.get("customer_type", "standard").lower()
                c_type = CustomerType.vip if cust_type_str == "vip" else CustomerType.standard
                cust = vip_cust if c_type == CustomerType.vip else std_cust

                c = Complaint(
                    complaint_code=f"CMP-{idx:05d}",
                    title=title,
                    description=desc,
                    normalized_text=sanitize_input(desc),
                    content_hash=chash,
                    product_or_service=row.get("product_or_service", ""),
                    order_reference=row.get("order_reference", ""),
                    customer_type=c_type,
                    channel=Channel.web,
                    status=ComplaintStatus.analyzed,
                    customer_id=cust.id if cust else None,
                    submitted_by_id=admin_user.id if admin_user else None,
                    latest_update="Analyzed and verified by Ground-Truth Pipeline.",
                )
                db.add(c)
                existing_hashes.add(chash)
                added += 1

                if added % 100 == 0:
                    db.flush()
                    print(f"  Flushed {added} new complaints...")

        print(f"Successfully added {added} new complaints. Total in DB: {existing_count + added}")

if __name__ == "__main__":
    seed_complaints()
