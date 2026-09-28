import sys
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database.session import session_scope
from database.models import Complaint, Customer, CustomerType, Channel, ComplaintStatus, User
from complaint_processing.preprocess import content_hash, sanitize_input

additional_samples = [
    ("Delayed Express Shipment", "Express shipment NC-200001 for NimbusWatch Pro has been delayed over 72 hours without status.", "NimbusWatch Pro", "NC-200001", CustomerType.vip),
    ("Defective Screen Flicker", "The display on ForgeTab Ultra order NC-200002 keeps flickering rapidly and causes eye strain.", "ForgeTab Ultra", "NC-200002", CustomerType.standard),
    ("Overbilling on Discounted Item", "The promotional code SAVE20 was not applied to order NC-200003 for AuraPods Max.", "AuraPods Max", "NC-200003", CustomerType.standard),
    ("Wrong Color Delivered", "Ordered matte black NovaCharge Stand but received bright neon green under order NC-200004.", "NovaCharge Stand", "NC-200004", CustomerType.standard),
    ("Missing Power Adapter", "The box for LumenDesk 50W NC-200005 did not contain the AC power brick adapter.", "LumenDesk 50W", "NC-200005", CustomerType.standard),
    ("Unauthorized Transaction Alert", "I noticed a suspicious charge for order NC-200006 on my statement that I did not place.", "NimbusCare Sub", "NC-200006", CustomerType.vip),
    ("Battery Drain Issue", "The battery on PulseBand 2 drains in less than 3 hours after full charge. Order NC-200007.", "PulseBand 2", "NC-200007", CustomerType.standard),
    ("Package Left in Rain", "Courier dumped package NC-200008 outside in heavy rain, water damaged the product carton.", "AuraSound Bar", "NC-200008", CustomerType.standard),
    ("Software Crash on Startup", "NimbusDesk companion software crashes immediately when opening on Windows 11 for NC-200009.", "NimbusDesk App", "NC-200009", CustomerType.standard),
    ("Refund Not Credited After 14 Days", "Return tracking confirmed delivered 14 days ago for NC-200010, still no refund credited.", "CartDock Pro", "NC-200010", CustomerType.vip),
    ("Cracked Lens on Arrival", "Camera lens on AuraCam 4K arrived shattered inside unopened factory box. Order NC-200011.", "AuraCam 4K", "NC-200011", CustomerType.standard),
    ("Misleading Product Dimensions", "The dimensions on the product page for ForgeDesk NC-200012 are 10cm smaller than advertised.", "ForgeDesk", "NC-200012", CustomerType.standard),
    ("Courier Driver Rudeness", "Delivery driver for order NC-200013 refused to deliver to doorstep and threw the package.", "NovaCharge Slim", "NC-200013", CustomerType.standard),
    ("Double Subscription Charge", "My card was debited twice on the 1st of the month for NimbusCloud NC-200014.", "NimbusCloud", "NC-200014", CustomerType.standard),
    ("Warranty Claim Denied Incorrectly", "Support denied my valid 1-year warranty claim for order NC-200015 citing invalid serial.", "AuraSound Max", "NC-200015", CustomerType.vip),
    ("Missing Manual and Accessories", "All connecting cables and user manual were missing from package NC-200016.", "CartHub Ultra", "NC-200016", CustomerType.standard),
    ("Noise Distortion at High Volume", "Left speaker exhibits severe rattling buzzing sound at 70% volume for NC-200017.", "AuraSound Mini", "NC-200017", CustomerType.standard),
    ("Gift Card Balance Deducted But Order Failed", "Balance was taken from gift card for NC-200018 but order status shows failed.", "Gift Card", "NC-200018", CustomerType.standard),
    ("Late Delivery for Birthday Event", "Guaranteed delivery date missed for birthday gift order NC-200019 by three days.", "PulseWatch Kid", "NC-200019", CustomerType.vip),
    ("Product Overheated During First Charge", "Charging cradle for NC-200020 became too hot to touch within 15 minutes of plugging in.", "PowerCradle", "NC-200020", CustomerType.standard),
    ("Incorrect Item In Box", "Outer carton says 512GB SSD but inside was a 128GB drive for NC-200021.", "NimbusDrive", "NC-200021", CustomerType.standard),
    ("Damaged Seal on Security Sensitive Device", "Factory tamper seal was broken when courier handed over parcel NC-200022.", "ForgeKey Auth", "NC-200022", CustomerType.vip),
    ("Invoice Not Received for Tax Purposes", "Company tax registration invoice missing for corporate order NC-200023.", "Corporate Pack", "NC-200023", CustomerType.standard),
    ("Broken Latch on Carry Case", "Zipper and magnetic latch on travel case NC-200024 came detached immediately.", "TravelCase Pro", "NC-200024", CustomerType.standard),
    ("Faulty Power Button", "Power toggle switch on AuraLamp NC-200025 is physically jammed inside housing.", "AuraLamp", "NC-200025", CustomerType.standard),
    ("Firmware Update Brick Device", "After installing official update on NC-200026, device refuses to boot or reset.", "ForgePad V2", "NC-200026", CustomerType.vip),
    ("Courier Claimed Address Not Found", "Courier marked address incomplete for NC-200027 despite landmark clearly provided.", "NovaCharge 100", "NC-200027", CustomerType.standard),
]

def add_remaining():
    with session_scope() as db:
        admin_user = db.query(User).first()
        std_cust = db.query(Customer).filter(Customer.customer_type == CustomerType.standard).first()
        vip_cust = db.query(Customer).filter(Customer.customer_type == CustomerType.vip).first()
        
        existing_codes = {c.complaint_code for c in db.query(Complaint.complaint_code).all()}
        next_num = 1
        
        for title, desc, prod, order, c_type in additional_samples:
            while f"CMP-{next_num:05d}" in existing_codes:
                next_num += 1
            code = f"CMP-{next_num:05d}"
            existing_codes.add(code)
            
            cust = vip_cust if c_type == CustomerType.vip else std_cust
            c = Complaint(
                complaint_code=code,
                title=title,
                description=desc,
                normalized_text=sanitize_input(desc),
                content_hash=content_hash(desc),
                product_or_service=prod,
                order_reference=order,
                customer_type=c_type,
                channel=Channel.web,
                status=ComplaintStatus.analyzed,
                customer_id=cust.id if cust else None,
                submitted_by_id=admin_user.id if admin_user else None,
                latest_update="Analyzed and verified by Ground-Truth Pipeline.",
            )
            db.add(c)
            next_num += 1
            
        db.flush()
        final_count = db.query(Complaint).count()
        print(f"Final Complaints count in DB: {final_count}")

if __name__ == "__main__":
    add_remaining()
