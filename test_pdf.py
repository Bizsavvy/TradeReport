import os
import jinja2
from xhtml2pdf import pisa

def test_pdf():
    context = {
        "start_date": "2023-01-01",
        "end_date": "2023-03-01",
        "roi": "10.5",
        "net_profit": "$5,000",
        "win_rate": "55.0",
        "profit_factor": "1.5",
        "total_trades": 100,
        "winning_trades_count": 55,
        "max_drawdown_percent": "2.5",
        "max_drawdown_usd": "$2,000",
        "avg_win": "$200",
        "avg_loss_abs": "$100",
        "initial_deposit": "$50,000",
        "initial_deposit_rounded": "$50,000",
        "growth_balance_rounded": "$55,000",
        "net_deposits_withdrawals": "$0",
        "gross_profit": "$11,000",
        "gross_loss_abs": "$6,000",
        "total_fees": "$500",
        "current_balance": "$55,000",
        "conclusion_text": "This reflects a highly profitable, active trading strategy."
    }

    env = jinja2.Environment(loader=jinja2.FileSystemLoader("."))
    template = env.get_template("report_template.html")
    
    # Load CSS
    with open("report_style.css", "r", encoding="utf-8") as f:
        embedded_css = f.read()
    
    context["embedded_css"] = embedded_css
    html_out = template.render(context)
    
    with open("test_xhtml2pdf.pdf", "wb") as pdf_file:
        pisa_status = pisa.CreatePDF(html_out, dest=pdf_file)
    print("PDF Generation complete. Errors:", pisa_status.err)

if __name__ == "__main__":
    test_pdf()
