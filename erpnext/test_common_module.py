# Common Module - Present in Main Repository
# This is a shared module that should be present in all forks

def validate_email(email):
    """Basic email validation"""
    if '@' not in email or '.' not in email:
        return False, "Invalid email format"
    return True, "Valid email"

def calculate_tax(amount, tax_rate=18):
    """Standard tax calculation"""
    return (amount * tax_rate) / 100
