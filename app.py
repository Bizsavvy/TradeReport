import os
import tempfile
import sys
import platform
import subprocess
import tarfile
import urllib.request
import stat
import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
import jinja2
import pdfkit
from pypdf import PdfWriter

@st.cache_resource
def setup_linux_wkhtmltopdf():
    wk_path = "/tmp/wk_bin/wkhtmltox/bin/wkhtmltopdf"
    if not os.path.exists(wk_path):
        os.makedirs("/tmp/wk_bin", exist_ok=True)
        tar_path = "/tmp/wkhtmltox.tar.xz"
        url = "https://github.com/wkhtmltopdf/wkhtmltopdf/releases/download/0.12.4/wkhtmltox-0.12.4_linux-generic-amd64.tar.xz"
        
        try:
            urllib.request.urlretrieve(url, tar_path)
            with tarfile.open(tar_path, "r:xz") as tar:
                def is_within_directory(directory, target):
                    abs_directory = os.path.abspath(directory)
                    abs_target = os.path.abspath(target)
                    prefix = os.path.commonprefix([abs_directory, abs_target])
                    return prefix == abs_directory

                def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
                    for member in tar.getmembers():
                        member_path = os.path.join(path, member.name)
                        if not is_within_directory(path, member_path):
                            raise Exception("Attempted Path Traversal in Tar File")
                    tar.extractall(path, members, numeric_owner=numeric_owner)

                safe_extract(tar, "/tmp/wk_bin")
            
            # Make sure it's executable
            st_bin = os.stat(wk_path)
            os.chmod(wk_path, st_bin.st_mode | stat.S_IEXEC)
        except Exception as e:
            st.error(f"Failed to download or extract wkhtmltopdf: {e}")
            return None
            
    return wk_path

st.set_page_config(page_title="Trading Performance Report Generator", layout="wide")

st.title("Trading Performance Report Generator")
st.markdown("Upload your MT4/MT5 Trading Statement (.html format) to generate a detailed performance report.")

user_starting_balance = st.number_input("Enter Starting Capital ($)", min_value=1.0, value=1000.0, step=100.0, help="If your HTML statement does not start at $0, enter the account's starting balance here to accurately calculate ROI and Drawdowns.")

uploaded_file = st.file_uploader("Upload HTML Statement", type=["html", "htm"])

if uploaded_file is not None:
    html_content = uploaded_file.getvalue().decode("utf-8")
    
    with st.spinner("Parsing Trading Data..."):
        try:
            # Parse HTML and find tables
            tables = pd.read_html(html_content)
        except Exception as e:
            st.error(f"Failed to parse tables from HTML: {e}")
            tables = []
            
        trade_df = None
        # Try finding the transactions table
        for tbl in tables:
            str_cols = [str(c).lower() for c in tbl.columns]
            # Check if headers are already correct
            if any('ticket' in c for c in str_cols) and any('profit' in c for c in str_cols):
                trade_df = tbl
                break
            
            # Or if it's buried in the first few rows
            header_idx = None
            for i, row in tbl.head(10).iterrows():
                row_str = ' '.join([str(val).lower() for val in row.values])
                if 'ticket' in row_str and 'profit' in row_str:
                    header_idx = i
                    break
            
            if header_idx is not None:
                tbl.columns = tbl.iloc[header_idx]
                tbl = tbl.iloc[header_idx+1:].reset_index(drop=True)
                trade_df = tbl
                break
                
        if trade_df is None and len(tables) > 0:
            # Fallback to the largest table
            trade_df = sorted(tables, key=len, reverse=True)[0]
            
        if trade_df is not None and not trade_df.empty:
            # Normalize column names
            trade_df.columns = [str(c).strip().lower() for c in trade_df.columns]
            
            profit_col = next((c for c in trade_df.columns if 'profit' in c), None)
            type_col = next((c for c in trade_df.columns if 'type' in c), None)
            
            if profit_col:
                # Clean profit column
                trade_df[profit_col] = pd.to_numeric(trade_df[profit_col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce')
                
                # Check for "Summary:" or "Closed P/L:" which usually indicates the end of trades
                first_summary_idx = trade_df.index[
                    trade_df.astype(str).apply(lambda row: row.str.contains('Closed P/L:|Summary:|Open Trades:', case=False).any(), axis=1)
                ].min()
                
                if pd.notna(first_summary_idx):
                    trade_df = trade_df.iloc[:first_summary_idx].copy()
                
                # Split deposits (balance) vs actual trades
                if type_col:
                    balance_mask = trade_df[type_col].astype(str).str.lower().str.contains('balance', na=False)
                    balance_df = trade_df[balance_mask]
                    trades = trade_df[~balance_mask].dropna(subset=[profit_col])
                    
                    # Also drop trades where Type is not a trade string (e.g. string noise)
                    valid_types = ['buy', 'sell']
                    trades = trades[trades[type_col].astype(str).str.lower().isin(valid_types)]
                else:
                    balance_df = pd.DataFrame()
                    trades = trade_df.dropna(subset=[profit_col])
                    
                initial_deposit = balance_df[profit_col].sum() if not balance_df.empty else 0
                
                # Basic calculations
                winning_trades = trades[trades[profit_col] > 0]
                losing_trades = trades[trades[profit_col] < 0]
                
                total_trades = len(trades)
                gross_profit = winning_trades[profit_col].sum()
                gross_loss = losing_trades[profit_col].sum()
                net_profit = gross_profit + gross_loss
                
                win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
                profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')
                avg_win = winning_trades[profit_col].mean() if len(winning_trades) > 0 else 0
                avg_loss = losing_trades[profit_col].mean() if len(losing_trades) > 0 else 0
                
                # Fees & Comm
                total_fees = 0
                comm_col = next((c for c in trade_df.columns if 'commission' in c or 'axes' in c), None) # 'axes' for Taxes
                swap_col = next((c for c in trade_df.columns if 'swap' in c), None)
                
                if comm_col:
                    t_fees = pd.to_numeric(trades[comm_col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').sum()
                    total_fees += t_fees
                if swap_col:
                    s_fees = pd.to_numeric(trades[swap_col].astype(str).str.replace(r'[^\d.-]', '', regex=True), errors='coerce').sum()
                    total_fees += s_fees
                
                # Correct Net Profit (including fees)
                net_profit = net_profit + total_fees 
                
                # Starting Balance Logic
                initial_deposit = user_starting_balance
                net_deposits_withdrawals = balance_df[profit_col].sum() if not balance_df.empty else 0
                
                # Drawdown estimation
                trades_copy = trades.copy()
                trades_copy['cumulative'] = trades_copy[profit_col].cumsum()
                trades_copy['high_water_mark'] = trades_copy['cumulative'].cummax()
                trades_copy['drawdown'] = trades_copy['high_water_mark'] - trades_copy['cumulative']
                max_drawdown_usd = trades_copy['drawdown'].max() if len(trades) > 0 else 0
                
                max_drawdown_percent = (max_drawdown_usd / initial_deposit) * 100 if initial_deposit > 0 else 0
                
                current_balance = initial_deposit + net_deposits_withdrawals + net_profit
                
                st.success(f"Parsed {total_trades} trades.")
                
                time_col = next((c for c in trade_df.columns if 'time' in c), None)
                start_date, end_date = "Extracted Date", "Extracted Date"
                if time_col:
                    dates = pd.to_datetime(trade_df[time_col], errors='coerce').dropna()
                    if not dates.empty:
                        start_date = dates.min().strftime('%b %d, %Y')
                        end_date = dates.max().strftime('%b %d, %Y')
                        
                # Dynamic ROI
                roi = ((net_profit) / initial_deposit * 100) if initial_deposit > 0 else 0
                
                st.header("Trading Performance Report")
                st.write(f"**Account Analysis:** {start_date} – {end_date}")
                if roi > 0:
                    pf_display = f"{profit_factor:.2f}" if profit_factor != float('inf') else 'N/A'
                    st.markdown(f"This account has demonstrated exceptional high-growth performance over the analyzed period, achieving a **+{roi:.1f}% Return on Investment (ROI)** while maintaining a controlled risk profile. The strategy exhibits a high win rate ({win_rate:.1f}%) combined with a strong Profit Factor ({pf_display}), indicating a highly efficient and profitable trading system.")
                else:
                    pf_display = f"{profit_factor:.2f}" if profit_factor != float('inf') else 'N/A'
                    st.markdown(f"This account has shown a **{roi:.1f}% Return on Investment (ROI)** over the analyzed period. The strategy exhibits a win rate of ({win_rate:.1f}%) and a Profit Factor of ({pf_display}).")
                
                st.subheader("1. Key Performance Indicators (KPIs)")
                kpi_data = {
                    "Metric": ["Net Profit", "Total ROI", "Profit Factor", "Win Rate", "Total Trades"],
                    "Value": [f"${net_profit:,.2f}", f"+{roi:.1f}%", f"{profit_factor:.2f}" if profit_factor != float('inf') else "N/A", f"{win_rate:.1f}%", str(total_trades)],
                    "Meaning": [
                        "Total profit secured after all fees/losses.",
                        f"Return on the initial deposit of ${initial_deposit:,.0f}.",
                        f"For every $1 lost, the account made ${profit_factor:.2f}." if profit_factor != float('inf') else "No losses occurred.",
                        f"Won {int(len(winning_trades))} out of {total_trades} trades taken.",
                        "Total executions during the period."
                    ]
                }
                st.table(pd.DataFrame(kpi_data))
                
                st.subheader("2. Risk & Stability Analysis")
                st.markdown(f"""
- **Max Drawdown:** {max_drawdown_percent:.2f}% (${max_drawdown_usd:,.2f})
- *Analysis:* The drawdown is moderate relative to the aggressive growth. The account recovered from its deepest dip quickly, validating the strategy's resilience.

- **Average Trade Outcome:**
- **Avg. Win:** +${avg_win:,.2f}
- **Avg. Loss:** -${abs(avg_loss):,.2f}
- *Analysis:* The strategy relies on a high win rate ({win_rate:.0f}%) rather than a high Risk-to-Reward ratio. The average win and loss are nearly 1:1, meaning the high accuracy is the primary driver of profit.
                """)
                
                st.subheader("3. Detailed Financial Breakdown")
                
                # Format Net Deposits correctly (e.g. -$2,600 instead of $-2,600)
                if net_deposits_withdrawals > 0:
                    net_dep_str = f"+${net_deposits_withdrawals:,.2f}"
                elif net_deposits_withdrawals < 0:
                    net_dep_str = f"-${abs(net_deposits_withdrawals):,.2f}"
                else:
                    net_dep_str = "$0.00"
                    
                st.markdown(f"""
- **Gross Profit:** ${gross_profit:,.2f} (Total money gained from winning trades)
- **Gross Loss:** -${abs(gross_loss):,.2f} (Total money lost from losing trades)
- **Net Result:** ${net_profit:,.2f} (Gross Profit minus Gross Loss & Fees)
- **Initial Deposit:** ~${initial_deposit:,.2f}
- **Net Deposits/Withdrawals:** {net_dep_str}
- **Current Balance:** ~${current_balance:,.2f}
                """)
                
                st.subheader("Conclusion")
                growth_balance = initial_deposit + net_profit
                
                if net_profit > 0:
                    conclusion_text = f"This statement reflects a highly profitable, active trading strategy. The system has successfully compounded the account from ~${initial_deposit:,.0f} to over ${growth_balance:,.0f} in the analyzed period. The metrics suggest a disciplined approach that capitalizes frequently on market movements (high win rate) while keeping losses within a recoverable range (stable drawdown)."
                else:
                    conclusion_text = f"This statement reflects a challenging trading period, with the account moving from ~${initial_deposit:,.0f} to ~${growth_balance:,.0f}. The strategy metrics suggest room for improvement in risk management or trade execution."
                
                st.write(conclusion_text)
                
                st.markdown("View the full account history here:")
                
                if not trades_copy.empty and 'cumulative' in trades_copy.columns:
                    chart_data = initial_deposit + trades_copy['cumulative']
                    st.line_chart(chart_data.reset_index(drop=True), height=250)
                
                st.markdown("---")
                
                # Prepare Jinja context
                context = {
                    "start_date": start_date,
                    "end_date": end_date,
                    "roi": f"{roi:.1f}",
                    "net_profit": f"${net_profit:,.2f}",
                    "win_rate": f"{win_rate:.1f}",
                    "profit_factor": f"{profit_factor:.2f}" if profit_factor != float('inf') else "N/A",
                    "total_trades": total_trades,
                    "winning_trades_count": int(len(winning_trades)),
                    "max_drawdown_percent": f"{max_drawdown_percent:.2f}",
                    "max_drawdown_usd": f"${max_drawdown_usd:,.2f}",
                    "avg_win": f"${avg_win:,.2f}",
                    "avg_loss_abs": f"${abs(avg_loss):,.2f}",
                    "initial_deposit": f"${initial_deposit:,.2f}",
                    "initial_deposit_rounded": f"${initial_deposit:,.0f}",
                    "growth_balance_rounded": f"${growth_balance:,.0f}",
                    "net_deposits_withdrawals": net_dep_str,
                    "gross_profit": f"${gross_profit:,.2f}",
                    "gross_loss_abs": f"${abs(gross_loss):,.2f}",
                    "total_fees": f"${total_fees:,.2f}",
                    "current_balance": f"${current_balance:,.2f}",
                    "conclusion_text": conclusion_text
                }
                
                # Load CSS for embedded HTML export
                embedded_css = ""
                css_path = os.path.join(os.path.dirname(__file__), "report_style.css")
                if os.path.exists(css_path):
                    with open(css_path, "r", encoding="utf-8") as css_file:
                        embedded_css = css_file.read()
                        
                context_html = context.copy()
                context_html["embedded_css"] = embedded_css
                
                with st.spinner("Generating Reports..."):
                    try:
                        env = jinja2.Environment(loader=jinja2.FileSystemLoader("."))
                        template = env.get_template("report_template.html")
                        
                        rendered_pdf_html = template.render(context)
                        # Render HTML standalone version with embedded CSS
                        standalone_html = template.render(context_html)
                        
                        # Append raw HTML to the bottom of the standalone HTML for reference
                        standalone_html += f"\n<hr style='margin-top: 50px; border-color: #2D3748;'>\n<h2>Raw Statement Data</h2>\n{html_content}"
                        
                        pdfkit_options = {
                            'enable-local-file-access': None,
                            'quiet': ''
                        }
                        
                        # Specify path to wkhtmltopdf dynamically based on OS
                        if platform.system() == "Windows":
                            path_wkhtmltopdf = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
                            config = pdfkit.configuration(wkhtmltopdf=path_wkhtmltopdf)
                        else:
                            # Streamlit Cloud (Linux) dynamically download binary
                            linux_bin_path = setup_linux_wkhtmltopdf()
                            if linux_bin_path and os.path.exists(linux_bin_path):
                                config = pdfkit.configuration(wkhtmltopdf=linux_bin_path)
                            else:
                                config = pdfkit.configuration()
                        
                        # Use temp files for pdfkit and pdf merging
                        temp_dir = tempfile.gettempdir()
                        tmp_html_path = os.path.join(temp_dir, "summary.html")
                        tmp_pdf_path = os.path.join(temp_dir, "summary.pdf")
                        raw_html_path = os.path.join(temp_dir, "raw.html")
                        raw_pdf_path = os.path.join(temp_dir, "raw.pdf")
                        final_pdf_path = os.path.join(temp_dir, "final_report.pdf")
                        
                        with open(tmp_html_path, "w", encoding="utf-8") as f:
                            f.write(rendered_pdf_html)
                            
                        pdfkit.from_file(tmp_html_path, tmp_pdf_path, options=pdfkit_options, configuration=config)
                        
                        with open(raw_html_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                            
                        try:
                            pdfkit.from_file(raw_html_path, raw_pdf_path, options=pdfkit_options, configuration=config)
                        except Exception as raw_e:
                            st.warning(f"Could not convert raw HTML to PDF easily (it might be too complex or lacking styles). Just using the summary PDF. Details: {raw_e}")
                            raw_pdf_path = None
                            
                        merger = PdfWriter()
                        merger.append(tmp_pdf_path)
                        if raw_pdf_path and os.path.exists(raw_pdf_path):
                            merger.append(raw_pdf_path)
                        merger.write(final_pdf_path)
                        merger.close()
                        
                        with open(final_pdf_path, "rb") as f:
                            pdf_data = f.read()
                            
                        st.download_button(
                            label="Download Merged Report (PDF)",
                            data=pdf_data,
                            file_name="Trading_Performance_Report.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )
                        
                        st.download_button(
                            label="Download Interactive HTML Report (.htm)",
                            data=standalone_html,
                            file_name="Trading_Performance_Report.htm",
                            mime="text/html",
                            use_container_width=True
                        )
                        
                        # Cleanup
                        for p in [tmp_html_path, tmp_pdf_path, raw_html_path, raw_pdf_path, final_pdf_path]:
                            if p and os.path.exists(p):
                                os.remove(p)
                                
                    except Exception as e:
                        st.error(f"Failed to generate PDF. Make sure wkhtmltopdf is installed and in your system PATH. Error details: {e}")
                
            else:
                st.error("Could not find a 'Profit' column in the uploaded statement. Please ensure this is a standard MT4/MT5 statement.")
        else:
            st.error("Could not parse transaction data from the HTML.")
