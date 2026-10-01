@echo off
REM Launch the Streamlit UI on port 8501 (headless = no email prompt)
streamlit run app/ui/main.py --server.port 8501 --server.address 127.0.0.1 --server.headless true
