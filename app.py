import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader
import json
import pandas as pd
from google import genai
from google.genai import types
import os
import tempfile
import zipfile
import time
import re
from typing import List, Dict, Set
from io import BytesIO
import logging
from datetime import datetime
import traceback
import matplotlib.ticker as mticker
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import seaborn as sns
import arabic_reshaper
from bidi.algorithm import get_display
from matplotlib.colors import LinearSegmentedColormap, BoundaryNorm
import plotly.express as px

# ============================================================================
# بخش 1: تنظیمات اولیه
# ============================================================================

def load_css(file_name):
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        st.error(f"فایل '{file_name}' پیدا نشد.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app_log.txt', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="AI Financial Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

load_css("style.css")


DEFAULT_API_KEYS = [
    "AIzaSyAo5oFZqsTRkUIqJRjoefWINWpbwPHbEn8",
    "AIzaSyBeLYGH4JS-fPHYdqKgUPotV2dpGZYZ2to",
    "AIzaSyDyj1DlOLAlbKzTLFP2tz95TcIca4oV0Vg"
]

if 'api_keys' not in st.session_state:
    st.session_state.api_keys = DEFAULT_API_KEYS.copy()

# ============================================================================
# بخش 2: احراز هویت
# ============================================================================

with open('config.yaml') as file:
    config = yaml.load(file, Loader=SafeLoader)

hashed_passwords = stauth.Hasher(["elnagh", "abc_fin_cba","123"]).generate()
config['credentials']['usernames']['admin']['password'] = hashed_passwords[0]
config['credentials']['usernames']['fin.analyst']['password'] = hashed_passwords[1]
config['credentials']['usernames']['h.khandani']['password'] = hashed_passwords[2]

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days'],
)

if 'authentication_status' not in st.session_state:
    st.session_state.authentication_status = None

if st.session_state.authentication_status is None:
    name, authentication_status, username = authenticator.login(location='main')
    st.session_state.authentication_status = authentication_status
    st.session_state.name = name
    st.session_state.username = username

if st.session_state.authentication_status == False:
    st.error('نام کاربری یا رمز عبور اشتباه است')
    st.stop()

if st.session_state.authentication_status == None:
    st.warning('لطفاً نام کاربری و رمز عبور خود را وارد کنید')
    st.stop()

# ============================================================================
# بخش 3: سایدبار
# ============================================================================

if st.session_state.authentication_status:
    
    with st.sidebar:
        st.header("⚙️ تنظیمات برنامه")
        st.divider()
        st.subheader("🔑 کلیدهای API")
        
        key_input_method = st.radio(
            "روش ورود کلیدها:",
            ["کلیدهای پیش‌فرض", "کلیدهای سفارشی"],
            label_visibility="collapsed"
        )
        
        if key_input_method == "کلیدهای پیش‌فرض":
            st.session_state.api_keys = DEFAULT_API_KEYS.copy()
            st.success(f"✅ {len(st.session_state.api_keys)} کلید لود شد")
        else:
            custom_keys_text = st.text_area(
                "کلیدهای API (جدا کننده enter):",
                height=120,
                placeholder="AIzaSy...\nAIzaSy..."
            )
            if custom_keys_text:
                st.session_state.api_keys = [key.strip() for key in custom_keys_text.split('\n') if key.strip()]
                st.success(f"✅ {len(st.session_state.api_keys)} کلید سفارشی")
            else:
                st.session_state.api_keys = DEFAULT_API_KEYS.copy()

        st.divider()
        # with st.expander("📋 راهنمای استفاده"):
        #     st.markdown("""
        #     - **مرحله ۱:** فایل‌های PDF را بارگذاری کنید.
        #     - **مرحله ۲:** روی دکمه "شروع تحلیل" کلیک کنید.
        #     - **مرحله ۳:** نتایج را مشاهده و دانلود کنید.
        #     """)

        st.markdown("<div style='margin-top: auto;'></div>", unsafe_allow_html=True)
        authenticator.logout('🚪 خروج از سیستم', 'sidebar')

    # ========================================================================
    # بخش 4: کلاس‌ها و توابع اصلی
    # ========================================================================

    class APIKeyManager:
        def __init__(self, api_keys: List[str]):
            if not api_keys:
                raise ValueError("API keys list cannot be empty.")
            self.api_keys = api_keys
            self.current_index = 0
            self.failures = {key: 0 for key in api_keys}
            self.max_failures = 3
            
        def get_next_key(self) -> str:
            attempts = 0
            while attempts < len(self.api_keys):
                key = self.api_keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                if self.failures.get(key, 0) < self.max_failures:
                    return key
                attempts += 1
            logger.warning("All API keys have failed, resetting failure counters")
            self.failures = {key: 0 for key in self.api_keys}
            return self.api_keys[0]
        
        def mark_failure(self, key: str):
            if key in self.failures:
                self.failures[key] += 1
                logger.warning(f"API key failure count for {key[:8]}...: {self.failures[key]}")
        
        def mark_success(self, key: str):
            if key in self.failures:
                self.failures[key] = 0

    api_key_manager = APIKeyManager(st.session_state.api_keys)

    def get_client_with_retry():
        api_key = api_key_manager.get_next_key()
        return genai.Client(api_key=api_key), api_key

    # ========================================================================
    # بخش 5: توابع پردازش فارسی و ادغام
    # ========================================================================

    def setup_persian_font():
        try:
            font_path = 'fonts/B Mitra_0.ttf'
            font = FontProperties(fname=font_path)
            plt.rc('font', family='B Mitra')
            
            return font
        except:
            logger.warning("فونت B Mitra یافت نشد.")
            return FontProperties()

    def process_persian_text(text):
        reshaped_text = arabic_reshaper.reshape(str(text))
        bidi_text = get_display(reshaped_text)
        return bidi_text

    def merge_excel_files(excel_files):
        if len(excel_files) < 2:
            return None
        
        merged_data = {}
        company_names = []
        
        try:
            for file_path in excel_files:
                df_summary = pd.read_excel(file_path, sheet_name="بخش1_خلاصه")
                if not df_summary.empty and 'نام_شرکت' in df_summary.columns:
                    company_names.append(df_summary['نام_شرکت'].iloc[0])
            
            if len(set(company_names)) > 1:
                logger.warning("شرکت‌ها هم‌نام نیستند")
                return None
            
            sheet_names = ["بخش1_خلاصه", "بند_تاکید_بر_مطالب_خاص", "بخش3_چک_لیست", "گزارش_قانونی"]
            
            for sheet in sheet_names:
                dfs = []
                for file_path in excel_files:
                    try:
                        df = pd.read_excel(file_path, sheet_name=sheet)
                        dfs.append(df)
                    except:
                        continue
                if dfs:
                    merged_data[sheet] = pd.concat(dfs, ignore_index=True)
            
            return merged_data if merged_data else None
        except Exception as e:
            logger.error(f"خطا در ادغام: {e}")
            return None

    # ========================================================================
    # بخش 6: توابع رسم نمودار (7 نمودار)
    # ========================================================================

    def plot_risk_trend(df, font):
        """نمودار خطی روند سطح ریسک - سایز استاندارد"""
        risk_level = ['پایین', 'متوسط', 'بالا', 'بحرانی']
        risk_mp = {'پایین': 0, 'متوسط': 1, 'بالا': 2, 'بحرانی': 3}
        
        df['year'] = pd.to_numeric(df['year'], errors='coerce').astype('Int64')
        df = df.dropna(subset=['year'])
        df["risk_mp"] = df['سطح_ریسک_کلی_بنا_به_گزارش'].map(risk_mp)
        df = df.sort_values('year')
        

        fig, ax = plt.subplots(figsize=(12, 7), facecolor='#f8f9fa')
        ax.set_facecolor('#ffffff')
        
   
        sns.lineplot(x='year', y='risk_mp', data=df, 
                    marker='o', markersize=12, linestyle='-', 
                    color='#e74c3c', linewidth=3, ax=ax)
        
 
        # ax.set_title(process_persian_text('روند سطح ریسک کلی در طول زمان'), 
        #             fontproperties=font, size=18, weight='bold', pad=20, color='#2c3e50')
        
        ax.set_xlabel(process_persian_text('سال مالی'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.set_ylabel(process_persian_text('سطح ریسک'), fontproperties=font, size=14, weight='bold', color='#34495e')

        ax.set_yticks(list(risk_mp.values()))
        y_labels = [process_persian_text(label) for label in risk_mp.keys()]
        ax.set_yticklabels(y_labels, fontproperties=font, fontsize=12)
        
    
        years = sorted(df['year'].unique())
        ax.set_xticks(years)
        ax.set_xticklabels([int(y) for y in years], fontproperties=font, fontsize=12)
        
        ax.set_ylim(-0.5, len(risk_level) - 0.5)
        
    
        ax.grid(True, linestyle='--', alpha=0.3, color='#bdc3c7')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#bdc3c7')
        ax.spines['bottom'].set_color('#bdc3c7')
        
        plt.tight_layout()
        return fig


    def plot_opinion_trend(df, font):
        """نمودار خطی روند اظهارنظر - سایز استاندارد"""
        opinion_mp = {'مقبول': 0, 'مشروط': 1, 'مردود': 2, 'عدم اظهارنظر': 3}
        
        df['year'] = pd.to_numeric(df['year'], errors='coerce').astype('Int64')
        df = df.dropna(subset=['year'])
        df["opinion_mp"] = df['نوع_اظهارنظر'].map(opinion_mp)
        df = df.sort_values('year')
        
        fig, ax = plt.subplots(figsize=(12, 7), facecolor='#f8f9fa')
        ax.set_facecolor('#ffffff')

        sns.lineplot(x='year', y='opinion_mp', data=df, 
                    marker='o', markersize=12, linestyle='-', 
                    color="#db34ba", linewidth=3, ax=ax)
        
        # ax.set_title(process_persian_text('روند اظهارنظر حسابرس در طول زمان'), 
        #             fontproperties=font, size=18, weight='bold', pad=20, color='#2c3e50')
        
        ax.set_xlabel(process_persian_text('سال مالی'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.set_ylabel(process_persian_text('نوع اظهار نظر'), fontproperties=font, size=14, weight='bold', color='#34495e')
        
        ax.set_yticks(list(opinion_mp.values()))
        y_labels = [process_persian_text(label) for label in opinion_mp.keys()]
        ax.set_yticklabels(y_labels, fontproperties=font, fontsize=12)
        
        years = sorted(df['year'].unique())
        ax.set_xticks(years)
        ax.set_xticklabels([int(y) for y in years], fontproperties=font, fontsize=12)
        
        ax.set_ylim(-0.5, 3.5)
        
        ax.grid(True, linestyle='--', alpha=0.3, color='#bdc3c7')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#bdc3c7')
        ax.spines['bottom'].set_color('#bdc3c7')
        
        plt.tight_layout()
        return fig

    def plot_checklist_heatmap(df, font):
        """نقشه حرارتی - سایز بزرگتر"""
        status_mapping = {
            'مصداق ندارد': 0,
            'بررسی شده - ریسک خاصی گزارش نشده': 1,
            'مسئله کلیدی منجر به اظهارنظر مشروط': 2,
            'ریسک بحرانی': 3
        }
        
        df["status_mp"] = df['وضعیت'].map(status_mapping)
        irrelevant_topics = [
            'آخرین صفحه گزارش حسابرس و بازرس که شامل امضا سازمان حسابرس میشود',
            'صفحه امضا های سازمان حسابرسی'
        ]
        df = df[~df['موضوع'].isin(irrelevant_topics)]
        
        heatmap_data = df.pivot_table(index='موضوع', columns='year', values='status_mp', aggfunc='max').fillna(0).astype(int)
        

        custom_colors = ["#E0E7FF", "#8EA4E9", "#A970DE", "#F46E52"]
        custom_cmap = LinearSegmentedColormap.from_list("modern_risk", custom_colors, N=4)
        norm = BoundaryNorm(boundaries=[-0.5, 0.5, 1.5, 2.5, 3.5], ncolors=4)
        
        # ✅ سایز بزرگ برای heatmap: 16x10
        fig, ax = plt.subplots(figsize=(12, 7.5), facecolor='#f8f9fa')
        
        sns.heatmap(
            heatmap_data, annot=True, cmap=custom_cmap, norm=norm,
            linewidths=2.5, linecolor='white', fmt='d', cbar=True, ax=ax,
            annot_kws={'fontsize': 11, 'weight': 'bold'},
            cbar_kws={'shrink': 0.8}
        )
        
        # ax.set_title(process_persian_text('نقشه حرارتی ریسک‌های کلیدی در طول زمان'),
        #             fontproperties=font, size=20, weight='bold', pad=25, color='#2c3e50')
        ax.set_xlabel(process_persian_text('سال مالی'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.set_ylabel(process_persian_text('موضوعات کلیدی'), fontproperties=font, size=14, weight='bold', color='#34495e')
        
        y_labels = [process_persian_text(label) for label in heatmap_data.index]
        x_labels = [process_persian_text(str(int(label))) for label in heatmap_data.columns]
        ax.set_yticklabels(y_labels, fontproperties=font, rotation=0, fontsize=11)
        ax.set_xticklabels(x_labels, fontproperties=font, rotation=0, fontsize=12)
        
        cbar = ax.collections[0].colorbar
        cbar.set_ticks(list(status_mapping.values()))
        cbar_labels = [process_persian_text(label) for label in status_mapping.keys()]
        cbar.set_ticklabels(cbar_labels, fontproperties=font, fontsize=11)
        cbar.ax.set_title(process_persian_text('سطح ریسک'), fontproperties=font, fontsize=13, pad=15)
        cbar.outline.set_linewidth(2)
        cbar.outline.set_edgecolor('#bdc3c7')
        
        for spine in ax.spines.values():
            spine.set_visible(False)
        
        plt.tight_layout()
        return fig
    

    def plot_risk_stacked_bar(df, font):
        """نمودار ستونی انباشته ریسک‌ها - فقط موارد دارای موضوعیت"""
        

        df = df[df.get('موضوعیت_دارد', True) == True].copy()
        
        if df.empty:
            # اگر داده‌ای نبود، یک figure خالی برگردان
            fig, ax = plt.subplots(figsize=(12, 8), facecolor='#f8f9fa')
            ax.text(0.5, 0.5, process_persian_text('داده‌ای برای نمایش وجود ندارد'), 
                    ha='center', va='center', fontsize=16, fontproperties=font)
            ax.axis('off')
            return fig
        
        risk_over_time = pd.crosstab(df['year'], df['دسته_اصلی_ریسک'])
        
        fig, ax = plt.subplots(figsize=(12, 10), facecolor='#f8f9fa')
        ax.set_facecolor('#ffffff')
        
        colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22']
        risk_over_time.plot(kind='bar', stacked=True, color=colors, ax=ax, width=0.8, edgecolor='white', linewidth=2)
        
        for c in ax.containers:
            labels = [int(v.get_height()) if v.get_height() > 0 else '' for v in c]
            ax.bar_label(c, labels=[f'{v}' if v else '' for v in labels], 
                        label_type='center', color='white', weight='bold', fontsize=11, fontproperties=font)
        
        # ax.set_title(process_persian_text('روند تعداد و ترکیب ریسک‌ها در طول زمان'), 
        #             fontproperties=font, size=18, weight='bold', pad=20, color='#2c3e50')
        ax.set_xlabel(process_persian_text('سال مالی'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.set_ylabel(process_persian_text('تعداد ریسک‌های برجسته شده'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.tick_params(axis='x', rotation=0, labelsize=12)
        ax.tick_params(axis='y', labelsize=12)
        
        ax.grid(True, axis='y', linestyle='--', alpha=0.3, color='#bdc3c7')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#bdc3c7')
        ax.spines['bottom'].set_color('#bdc3c7')
        
        legend = ax.get_legend()
        legend.set_title(process_persian_text('دسته اصلی ریسک'), prop=font)
        legend.set_frame_on(True)
        legend.get_frame().set_facecolor('#ffffff')
        legend.get_frame().set_edgecolor('#bdc3c7')
        legend.get_frame().set_linewidth(1.5)
        for text in legend.get_texts():
            text.set_text(process_persian_text(text.get_text()))
            text.set_fontproperties(font)
            text.set_fontsize(10)
        
        plt.tight_layout()
        return fig


    def plot_risk_sunburst(df):
        """📊 نمودار Sunburst ریسک‌ها - نسخه نهایی با طراحی مشابه تخلفات و مرکز با عنوان 'ریسک‌های برجسته'"""

        sunburst_data = df.groupby(['دسته_اصلی_ریسک', 'زیرشاخه_ریسک']).size().reset_index(name='تعداد')

        fig = px.sunburst(
            sunburst_data,
            path=[px.Constant("ریسک‌های برجسته"), 'دسته_اصلی_ریسک', 'زیرشاخه_ریسک'],
            values='تعداد',
            color='دسته_اصلی_ریسک',
            color_discrete_sequence=px.colors.qualitative.Set3,
            hover_data={'تعداد': True}
        )

        fig.update_layout(
            width=900,
            height=700,
            # title={
            #     'text': 'تحلیل سلسله‌مراتبی توزیع ریسک‌ها',
            #     'y': 0.98,
            #     'x': 0.5,
            #     'xanchor': 'center',
            #     'yanchor': 'top',
            #     'font': {'size': 22, 'family': 'Tahoma', 'color': '#2c3e50'}
            # },
            font_family="B Mitra_0",
            font_size=13,
            margin=dict(t=80, l=40, r=40, b=40),
            paper_bgcolor='#f8f9fa',
            plot_bgcolor='#ffffff',
            hovermode='closest',
            hoverlabel=dict(
                bgcolor="white",
                font_size=14,
                font_family="B Mitra",
                align="right"
            ),
            # ✅ افزودن متن مرکز

        )

        # تنظیمات ترسیم جزئیات نمودار
        fig.update_traces(
            textinfo="label",
            insidetextorientation='horizontal',  # افقی برای خوانایی بهتر
            marker=dict(
                line=dict(color='white', width=2.5)
            ),
            textfont=dict(
                size=12,
                family="B Mitra",
                color="black"
            ),
            hovertemplate='<b style="font-family:B Mitra">%{label}</b><br>' +
                        '<span style="font-family:B Mitra">تعداد: %{value}</span><br>' +
                        '<span style="font-family:B Mitra">نسبت: %{percentRoot:.1%}</span>' +
                        '<extra></extra>',
            hoverlabel=dict(
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor="#2c3e50",
                font=dict(family="B Mitra", size=13, color="black")
            )
        )

        return fig



    def plot_violations_stacked_bar(df, font):
        """نمودار ستونی انباشته تخلفات - فقط موارد دارای موضوعیت"""
        
        # ✅ فیلتر کردن فقط موارد دارای موضوعیت
        df = df[df.get('موضوعیت_دارد', True) == True].copy()
        
        if df.empty:
            # اگر داده‌ای نبود، یک figure خالی برگردان
            fig, ax = plt.subplots(figsize=(12, 8), facecolor='#f8f9fa')
            ax.text(0.5, 0.5, process_persian_text('داده‌ای برای نمایش وجود ندارد'), 
                    ha='center', va='center', fontsize=16, fontproperties=font)
            ax.axis('off')
            return fig
        
        violation_counts = pd.crosstab(df['year'], df['دسته_اصلی'])
        
        fig, ax = plt.subplots(figsize=(12, 10), facecolor='#f8f9fa')
        ax.set_facecolor('#ffffff')
        
        colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c']
        violation_counts.plot(kind='bar', stacked=True, color=colors, ax=ax, width=0.8, edgecolor='white', linewidth=2)
        
        for c in ax.containers:
            labels = [int(v.get_height()) if v.get_height() > 0 else '' for v in c]
            ax.bar_label(c, labels=[f'{v}' if v else '' for v in labels], 
                        label_type='center', color='white', weight='bold', fontsize=11, fontproperties=font)
        
        # ax.set_title(process_persian_text('روند تعداد و ترکیب تخلفات قانونی در طول زمان'), 
        #             fontproperties=font, size=18, weight='bold', pad=20, color='#2c3e50')
        ax.set_xlabel(process_persian_text('سال مالی'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.set_ylabel(process_persian_text('تعداد موارد عدم رعایت'), fontproperties=font, size=14, weight='bold', color='#34495e')
        ax.tick_params(axis='x', rotation=0, labelsize=12)
        ax.tick_params(axis='y', labelsize=12)
        
        ax.grid(True, axis='y', linestyle='--', alpha=0.3, color='#bdc3c7')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#bdc3c7')
        ax.spines['bottom'].set_color('#bdc3c7')
        
        legend = ax.get_legend()
        legend.set_title(process_persian_text('دسته اصلی تخلف'), prop=font)
        legend.set_frame_on(True)
        legend.get_frame().set_facecolor('#ffffff')
        legend.get_frame().set_edgecolor('#bdc3c7')
        legend.get_frame().set_linewidth(1.5)
        for text in legend.get_texts():
            text.set_text(process_persian_text(text.get_text()))
            text.set_fontproperties(font)
            text.set_fontsize(10)
        
        plt.tight_layout()
        return fig
    

    
    def plot_violations_sunburst(df):
        """نمودار Sunburst تخلفات - نسخه اصلاح شده با متن واضح"""
        
        sunburst_data = df.groupby(['دسته_اصلی', 'زیرشاخه']).size().reset_index(name='تعداد')

        fig = px.sunburst(
            sunburst_data,
            path=[px.Constant("تمام تخلفات"), 'دسته_اصلی', 'زیرشاخه'],
            values='تعداد',
            color='دسته_اصلی',
            # title='تحلیل ساختار و توزیع تخلفات قانونی',
            color_discrete_sequence=px.colors.qualitative.Set3,
            hover_data={'تعداد': True}
        )
        
        fig.update_layout(
            width=900,
            height=700,
            # title={
            #     'text': 'تحلیل ساختار و توزیع تخلفات قانونی',
            #     'y': 0.98,
            #     'x': 0.5,
            #     'xanchor': 'center',
            #     'yanchor': 'top',
            #     'font': {'size': 22, 'family': 'Tahoma', 'color': '#2c3e50'}
            # },
            font_family="B Mitra",
            font_size=13,
            margin=dict(t=80, l=40, r=40, b=40),
            paper_bgcolor='#f8f9fa',
            plot_bgcolor='#ffffff',
            # ✅ تنظیمات مهم برای نمایش صحیح hover
            hovermode='closest',
            hoverlabel=dict(
                bgcolor="white",
                font_size=14,
                font_family="B Mitra",
                align="right"
            )
        )
        
        fig.update_traces(
            textinfo="label",
            insidetextorientation='horizontal',  # ✅ افقی برای خوانایی بهتر
            marker=dict(
                line=dict(color='white', width=2.5)
            ),
            textfont=dict(
                size=12,  # ✅ فونت متوسط برای خوانایی بهتر
                family="B Mitra",
                color="black"
            ),
            # ✅ Hover template ساده و واضح با فارسی
            hovertemplate='<b style="font-family:B Mitra">%{label}</b><br>' +
                        '<span style="font-family:B Mitra">تعداد: %{value}</span><br>' +
                        '<span style="font-family:B Mitra">نسبت: %{percentRoot:.1%}</span>' +
                        '<extra></extra>',
            hoverlabel=dict(
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor="#2c3e50",
                font=dict(family="B Mitra", size=13, color="black")
            )
        )
        
        return fig
    # ========================================================================
    # بخش 7: کلاس FinancialAnalyzer (ساده شده)
    # ========================================================================

    class FinancialAnalyzer:
        def __init__(self):
            self.response_schema = {
                "type": "object",
                "properties": {
                    "تحلیل_جامع_گزارش_حسابرسی": {
                        "type": "object",
                        "description": "ساختار اصلی که تحلیل کامل گزارش حسابرس مستقل و بازرس قانونی را در خود جای می‌دهد.",
                        "properties": {
                            "بخش۱_خلاصه_و_اطلاعات_کلیدی": {
                                "type": "object",
                                "description": "شامل اطلاعات اولیه گزارش و نتیجه‌گیری‌های اصلی در یک نگاه.",
                                "properties": {
                                    "نام_شرکت": {"type": "string", "description": "نام کامل شرکت از روی جلد گزارش."},
                                    "نام_حسابرس": {"type": "string", "description": "نام موسسه حسابرسی."},
                                    "دوره_مالی": {"type": "string", "description": "دوره مالی مورد رسیدگی، مثلا: 'سال مالی منتهی به ۲۹ اسفند ۱۳۹۸'."},
                                    "نوع_اظهارنظر": {
                                        "type": "string",
                                        "description": "یکی از موارد: مقبول، مشروط، مردود، عدم اظهارنظر.",
                                        "enum": ["مقبول", "مشروط", "مردود", "عدم اظهارنظر"]
                                    },
                                    "سطح_ریسک_کلی_بنا_به_گزارش": {
                                        "type": "string",
                                        "description": "سطح ریسک کلی استنباط شده از گزارش حسابرس مستقل و بازرس قانونی بنا به متن گزارش و شواهد و آماره های بیان شده از دیدگاه حسابرسی",
                                        "enum": ["پایین", "متوسط", "بالا", "بحرانی"]
                                    },
                                    "جزییات_سطح_ریسک_تعیین_شده": {
                                        "type": "string",
                                        "description": "جزییات و دلیل سطح ریسک کلی استنباط شده از گزارش."
                                    },
                                    "نکات_کلیدی_و_نتیجه_گیری": {
                                        "type": "array",
                                        "description": "آرایه‌ای از ۳ رشته شامل مهم‌ترین یافته‌ها و نتیجه‌گیری‌ها.",
                                        "items": {"type": "string"}
                                    }
                                },
                                "required": [
                                    "نام_شرکت", "نام_حسابرس", "دوره_مالی", "نوع_اظهارنظر",
                                    "سطح_ریسک_کلی_بنا_به_گزارش", "جزییات_سطح_ریسک_تعیین_شده",
                                    "نکات_کلیدی_و_نتیجه_گیری"
                                ]
                            },
                            "بخش۲_تجزیه_تحلیل_گزارش": {
                                "type": "object",
                                "description": "تجزیه و تحلیل ساختاریافته متن گزارش، بند به بند.",
                                "properties": {
                                    "بند_اظهارنظر": {
                                        "type": "object",
                                        "properties": {
                                            "نوع": {"type": "string"},
                                            "خلاصه_دلایل": {"type": "string"}
                                        },
                                        "required": ["نوع", "خلاصه_دلایل"]
                                    },
                                    "بند_مبانی_اظهارنظر": {
                                        "type": "object",
                                        "properties": {
                                            "موضوعیت_دارد": {"type": "boolean"},
                                            "موارد_مطرح_شده": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "شماره_مورد": {"type": "integer"},
                                                        "عنوان": {"type": "string"},
                                                        "شرح": {"type": "string"},
                                                        "نوع_دلیل": {
                                                            "type": "string",
                                                            "enum": ["محدودیت در رسیدگی", "انحراف از استانداردهای حسابداری", "سایر"]
                                                        }
                                                    },
                                                    "required": ["شماره_مورد", "عنوان", "شرح", "نوع_دلیل"]
                                                }
                                            }
                                        },
                                        "required": ["موضوعیت_دارد"]
                                    },
                                    "بند_تاکید_بر_مطالب_خاص": {
                                        "type": "object",
                                        "properties": {
                                            "موضوعیت_دارد": {"type": "boolean"},
                                            "موارد_مطرح_شده": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "ارجاع": {
                                                            "type": "object",
                                                            "properties": {
                                                                "شماره_بند": {"type": "string"},
                                                                "شماره_صفحه": {"type": "string"}
                                                            },
                                                            "required": ["شماره_بند", "شماره_صفحه"]
                                                        },
                                                        "عنوان": {"type": "string"},
                                                        "شرح": {"type": "string"},
                                                        "ریسک_برجسته_شده": {"type": "string"},
                                                        "دسته_اصلی_ریسک": {
                                                            "type": "string",
                                                            "enum": ["ریسک اعتباری", "ریسک بازار", "ریسک نقدینگی", "ریسک عملیاتی", "ریسک قانونی و تطبیق", "ریسک استراتژیک", "ریسک شهرت"]
                                                        },
                                                        "زیرشاخه_ریسک": {
                                                            "type": "string",
                                                            "enum": [
                                                                "ریسک نکول", "ریسک تمرکز", "ریسک طرف قرارداد", "ریسک کشور", "ریسک تضعیف وثایق", "ریسک وصول مطالبات",
                                                                "ریسک نرخ ارز", "ریسک نرخ سود", "ریسک قیمت کالا", "ریسک قیمت سهام", "ریسک نوسان ارزش سرمایه‌گذاری‌ها",
                                                                "ریسک تامین مالی", "ریسک نقدشوندگی بازار", "ریسک تسویه با نهادهای حاکمیتی",
                                                                "ریسک فرآیندهای داخلی", "ریسک فناوری اطلاعات و امنیت سایبری", "ریسک منابع انسانی", "ریسک تقلب", "ریسک رویدادهای خارجی", "ریسک مدل", "ریسک عدم کفایت پوشش بیمه‌ای",
                                                                "ریسک دعاوی حقوقی", "ریسک عدم رعایت مقررات", "ریسک قراردادها", "ریسک مالیاتی", "ریسک پولشویی و تامین مالی تروریسم", "ریسک حاکمیت شرکتی",
                                                                "ریسک رقابت", "ریسک تغییرات تکنولوژی", "ریسک تصمیمات مدیریتی", "ریسک پروژه‌ها و سرمایه‌گذاری‌های کلان", "ریسک ادغام و تملیک", "ریسک تغییرات کلان اقتصادی",
                                                                "ریسک رضایت مشتری", "ریسک وجهه عمومی", "ریسک روابط با ذینفعان"
                                                            ]
                                                        }
                                                    },
                                                    "required": ["ارجاع", "عنوان", "شرح", "ریسک_برجسته_شده", "دسته_اصلی_ریسک", "زیرشاخه_ریسک"]
                                                }
                                            }
                                        },
                                        "required": ["موضوعیت_دارد"]
                                    },
                                    "گزارش_رعایت_الزامات_قانونی": {
                                        "type": "object",
                                        "properties": {
                                            "موضوعیت_دارد": {"type": "boolean"},
                                            "تخلفات": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "ارجاع": {
                                                            "type": "object",
                                                            "properties": {
                                                                "شماره_بند": {"type": "string"},
                                                                "شماره_صفحه": {"type": "string"}
                                                            },
                                                            "required": ["شماره_بند", "شماره_صفحه"]
                                                        },
                                                        "عنوان_تخلف": {"type": "string"},
                                                        "شرح": {"type": "string"},
                                                        "مبانی_قانونی_و_استانداردها": {
                                                            "type": "array",
                                                            "items": {
                                                                "type": "string",
                                                                "enum": [
                                                                    "قانون پولی و بانکی کشور",
                                                                    "قانون عملیات بانکی بدون ربا‌ً",
                                                                    "آیین نامه ها و دستورالعمل‌های بانک مرکزی (مهمترین بخش)",
                                                                    "اساسنامه بانک",
                                                                    "قانون تجارت (در موارد مرتبط)",
                                                                    "استانداردهای حسابداری",
                                                                    "استانداردهای حسابرسی"
                                                                ]
                                                            }
                                                        },
                                                        "دسته_اصلی": {
                                                            "type": "string",
                                                            "description": "دسته اصلی تخلف بر اساس منشأ قانون.اگر یک تخلف به چند قانون مربوط است، اولویت با دسته‌ای است که خاص‌تر و مهم‌تر است. ",
                                                            "enum": [
                                                            "الزامات نهاد ناظر بانکی (بانک مرکزی)",
                                                            "الزامات بازار سرمایه (سازمان بورس)",
                                                            "قوانین حاکمیتی و شرکتی",
                                                            "قوانین مالیاتی، بیمه و بودجه",
                                                            "قوانین مبارزه با جرائم مالی",
                                                            "سایر قوانین و مقررات"
                                                            ]
                                                        },
                                                        "زیرشاخه": {
                                                            "type": "string",
                                                            "description": """ زیرشاخه دقیق تخلف شناسایی شده مرتبط با دسته اصلی شناسایی شده .(دقیقا از این مصادیق استفاده شود. مصادق منطبق با الگوی دسته_اصلی:[زیرشاخه ها] مانند 
                                                            :الزامات نهاد ناظر بانکی (بانک مرکزی)
                                                          [کفایت سرمایه و مدیریت سرمایه,
                                                            تسهیلات و تعهدات کلان,
                                                            معاملات با اشخاص وابسته,
                                                            طبقه‌بندی دارایی‌ها و ذخیره‌گیری,
                                                            مدیریت نقدینگی و سپرده قانونی,
                                                            نرخ سود و کارمزد خدمات,
                                                            الزامات ارزی,
                                                            واگذاری اموال و دارایی‌های مازاد,
                                                            الزامات صندوق ضمانت سپرده‌ها,[
                                                            الزامات بازار سرمایه (سازمان بورس):
                                                            [افشای اطلاعات,
                                                            حاکمیت شرکتی (دستورالعمل بورس),
                                                            تکالیف مجامع عمومی (مربوط به بورس),
                                                            معاملات با اشخاص وابسته (ضوابط بورس)],
                                                            قوانین حاکمیتی و شرکتی:
                                                            [نقض مفاد اساسنامه,
                                                            نقض قانون تجارت,
                                                            تکالیف ثبت شرکت‌ها,]
                                                            قوانین مالیاتی، بیمه و بودجه:
                                                            [مالیات عملکرد و تکلیفی,
                                                            مالیات بر ارزش افزوده (VAT),
                                                            بیمه تامین اجتماعی,
                                                            قوانین بودجه سنواتی و توسعه,]
                                                            قوانین مبارزه با جرائم مالی:
                                                            [مبارزه با پولشویی (AML),
                                                            مبارزه با تامین مالی تروریسم (CFT),]
                                                            سایر قوانین و مقررات:
                                                            [قانون کار,
                                                            قوانین شهرداری و محیط زیست,
                                                            حقوق مالکیت فکری,
                                                            سایر
                                                            ]
                                                            """,
                                                            
                                                        },

                                                        },
                                                        "required": [
                                                        "ارجاع",
                                                        "عنوان_تخلف",
                                                        "شرح",
                                                        "مبانی_قانونی_و_استانداردها",
                                                        "دسته_اصلی",
                                                        "زیرشاخه"
                                                        ]

                                                }
                                            }
                                        },
                                        "required": ["موضوعیت_دارد"]
                                    }
                                }
                            },
                            "بخش۳_چک_لیست_موضوعی": {
                                "type": "array",
                                "description":" باید تمام موضوع های زیر را چک کنید و در خروجی بیاورید هم مواردی که در گزارش آمده هم مواردی که نیامده",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "موضوع": {
                                            "type": "string",
                                            "enum": [
                                                "کفایت سرمایه", "تسعیر ارز و عملیات خارجی", "مالیات و جرائم مالیاتی",
                                                "تجدید ارزیابی دارایی‌های ثابت و نامشهود", "تعهدات ارزی و اختلاف با بانک مرکزی",
                                                "تهاتر(Barter)", "عدم دریافت تأییدیه‌های حسابداری", "مغایرت‌های حساب جاری بانک مرکزی",
                                                "نسبت کفایت سرمایه", "نسبت ها در چارچوب بازل(bazel Accords)",
                                                "(Facilities and Credits)تسهیلات و اعتبارات", "سود سهام دولت",
                                                "پروژه‌های اجرایی ناتمام", "معاملات با اشخاص وابسته", "ذخیره گیری",
                                                "صفحه امضا های سازمان حسابرسی"
                                            ]
                                        },
                                        "در_گزارش_آمده": {"type": "boolean"},
                                        "وضعیت": {
                                            "type": "string",
                                            "enum": [
                                                "مصداق ندارد", "بررسی شده - ریسک خاصی گزارش نشده",
                                                "مسئله کلیدی منجر به اظهارنظر مشروط", "ریسک بحرانی"
                                            ]
                                        },
                                        "جزئیات": {"type": "string"},
                                        "ارجاع": {
                                            "type": "object",
                                            "properties": {
                                                "شماره_بند": {"type": "string"},
                                                "شماره_صفحه": {"type": "string"}
                                            }
                                        }
                                    },
                                    "required": ["موضوع", "در_گزارش_آمده", "وضعیت", "جزئیات", "ارجاع"]
                                }
                            }
                        }
                    }
                },
                "required": ["تحلیل_جامع_گزارش_حسابرسی"]
            }
        
        def extract_table_from_page(self, file_content: bytes, filename: str, max_retries: int = 5) -> Dict:
            prompt = """لطفاً گزارش حسابرس را تحلیل کنید. نکته بسیار مهم برای بخش۳_چک_لیست_موضوعی:
            - باید تمام 16 موضوع زیر را چک کنید و در خروجی بیاورید
            - برای هر موضوع، فیلد "در_گزارش_آمده" را مشخص کنید (true یا false)
            - همه 16 موضوع باید در آرایه بخش۳_چک_لیست_موضوعی باشند"""
            for attempt in range(max_retries):
                try:
                    client, current_api_key = get_client_with_retry()
                    logger.info(f"Processing {filename} - Attempt {attempt + 1}")
                    response = client.models.generate_content(
                        model="gemini-2.5-pro",
                        contents=[types.Part.from_bytes(data=file_content, mime_type="application/pdf"), prompt],
                        config={'system_instruction': "شما تحلیلگر مالی هستید.", "response_mime_type": "application/json", "response_schema": self.response_schema, "temperature": 0.5}
                    )
                    if not response or not response.text:
                        raise ValueError("API response was empty")
                    data = json.loads(response.text)
                    api_key_manager.mark_success(current_api_key)
                    logger.info(f"Successfully processed {filename}")
                    return data
                except Exception as e:
                    logger.error(f"Error: {str(e)}")
                    api_key_manager.mark_failure(current_api_key)
                    if attempt < max_retries - 1:
                        time.sleep(min(15, (2 ** attempt)))
                    else:
                        raise
            raise Exception(f"Failed after {max_retries} attempts")

    # ========================================================================
    # بخش 8: توابع کمکی
    # ========================================================================

    def get_risk_class(risk_level):
        risk_classes = {'پایین': 'risk-low', 'متوسط': 'risk-medium', 'بالا': 'risk-high', 'بحرانی': 'risk-critical'}
        return risk_classes.get(risk_level, '')

    def flatten_reference_data(df):
        if 'ارجاع' in df.columns:
            df['شماره_بند'] = df['ارجاع'].apply(lambda x: x.get('شماره_بند', '') if isinstance(x, dict) else '')
            df['شماره_صفحه'] = df['ارجاع'].apply(lambda x: x.get('شماره_صفحه', '') if isinstance(x, dict) else '')
            df = df.drop('ارجاع', axis=1)
        return df

    def flatten_array_fields(df):
        for col in df.columns:
            df[col] = df[col].apply(lambda x: ", ".join(map(str, x)) if isinstance(x, list) else x)
        return df

    # ========================================================================
    # بخش 9: توابع UI
    # ========================================================================

    def create_header():
        st.markdown('<div class="modern-header"><h1>📊 تحلیلگر هوشمند صورت‌های مالی</h1></div>', unsafe_allow_html=True)

    def create_file_upload_section():
        st.markdown('<div class="modern-card"> <h2>📁 بارگذاری فایل‌ها</h2></div>', unsafe_allow_html=True)
        st.divider()
        upload_method = st.radio("روش بارگذاری:", ["فایل‌های جداگانه", "پوشه ZIP"], horizontal=True)
        uploaded_files = []
        if upload_method == "فایل‌های جداگانه":
            uploaded_files = st.file_uploader("فایل های PDF خود را اینجا بارگذاری کنید", type=['pdf'], accept_multiple_files=True)
        else:
            zip_file = st.file_uploader("فایل ZIP شامل PDF ها را انتخاب کنید", type=['zip'])
            if zip_file:
                try:
                    with zipfile.ZipFile(zip_file, 'r') as z:
                        uploaded_files = [{'name': os.path.basename(f.filename), 'content': z.read(f.filename)} for f in z.infolist() if f.filename.lower().endswith('.pdf')]
                    if uploaded_files:
                        st.success(f'✅ {len(uploaded_files)} فایل PDF استخراج شد')
                except Exception as e:
                    st.error(f'❌ خطا: {e}')
        return uploaded_files

    def create_processing_section(uploaded_files):
        """بخش آماده‌سازی و نمایش وضعیت فایل‌های ورودی"""
        if not uploaded_files:
            return None

        with st.container():
            st.subheader("🚀 آماده پردازش")
            st.divider()

            col1, col2, col3 = st.columns(3)
            total_size_mb = sum(
                len(f['content']) if isinstance(f, dict) else f.size
                for f in uploaded_files
            ) / (1024 * 1024)

            # کارت ۱: تعداد فایل‌ها
            with col1:
                st.markdown(f"""
                    <div class="metric-modern">
                        <p>تعداد فایل‌ها</p>
                        <div class="metric-value-box">
                            <div class="metric-value">{len(uploaded_files)}</div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

            # کارت ۲: حجم کل فایل‌ها
            with col2:
                st.markdown(f"""
                    <div class="metric-modern">
                        <p>حجم کل</p>
                        <div class="metric-value-box">
                            <div class="metric-value">{total_size_mb:.1f} MB</div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

            # کارت ۳: وضعیت آماده
            with col3:
                st.markdown(f"""
                    <div class="metric-modern metric-status-ready">
                        <p>وضعیت</p>
                        <div class="metric-value-box">
                            <div class="metric-value">آماده ✅</div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # دکمه شروع پردازش
            if st.button("🚀 شروع تحلیل", type="primary"):
                return process_files(uploaded_files)

        return None


    def process_files(uploaded_files):
        analyzer = FinancialAnalyzer()
        results = []
        st.markdown('<div class="modern-card"><h3>در حال پردازش...</h3></div>', unsafe_allow_html=True)
        progress_bar = st.progress(0)
        status_placeholder = st.empty()
        total_files = len(uploaded_files)
        start_time = time.time()
        for i, file in enumerate(uploaded_files):
            filename = file['name'] if isinstance(file, dict) else file.name
            file_content = file['content'] if isinstance(file, dict) else file.getvalue()
            status_placeholder.info(f'📄 در حال تحلیل فایل {i+1} از {total_files}: {filename}')
            try:
                result = analyzer.extract_table_from_page(file_content, filename)
                results.append((filename, result))
                status_placeholder.success(f'✅ **{filename}** با موفقیت تحلیل شد.')
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to process {filename}: {error_msg}\n{traceback.format_exc()}")
                results.append((filename, {"error": f"خطا در تحلیل: {error_msg}"}))
                status_placeholder.error(f'❌ خطا در تحلیل **{filename}**: {error_msg[:100]}...')
            progress_bar.progress((i + 1) / total_files)
        total_duration = time.time() - start_time
        status_placeholder.success(f'🎉 تحلیل تکمیل شد! {total_files} فایل در {total_duration/60:.1f} دقیقه پردازش شد.')
        return results
    

    @st.cache_data
    def convert_to_excel(results):


        temp_dir = tempfile.mkdtemp()
        excel_files = []
        
        for filename, data in results:
            try:
                if "error" in data:
                    continue
                
                report = data["تحلیل_جامع_گزارش_حسابرسی"]
                
                # ✅ استخراج سال مالی
                try:
                    company_name = report["بخش۱_خلاصه_و_اطلاعات_کلیدی"]["نام_شرکت"]
                    financial_year = report["بخش۱_خلاصه_و_اطلاعات_کلیدی"]["دوره_مالی"]
                    
                    year_match = re.search(r'(\d{4})', financial_year)
                    year = year_match.group(1) if year_match else "Unknown"
                    
                    clean_company_name = re.sub(r'[\\/:"*?<>|]+', "", company_name).strip()
                    if not clean_company_name:
                        clean_company_name = f"Company_{len(excel_files) + 1}"
                    
                    excel_filename = f"{clean_company_name}_{year}.xlsx"
                    
                except:
                    year = "Unknown"
                    excel_filename = f"Company_{len(excel_files) + 1}.xlsx"
                
                output_file = os.path.join(temp_dir, excel_filename)
                
                with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
                    
                    # ========================================
                    # بخش 1: خلاصه و اطلاعات کلیدی
                    # ========================================
                    try:
                        part1 = report["بخش۱_خلاصه_و_اطلاعات_کلیدی"]
                        df1 = pd.DataFrame.from_dict({
                            k: [v] if not isinstance(v, list) else [", ".join(v)] 
                            for k, v in part1.items()
                        })
                        # ✅ اضافه کردن ستون year
                        df1['year'] = year
                        df1.to_excel(writer, sheet_name="بخش1_خلاصه", index=False)
                    except Exception as e:
                        logger.warning(f"خطا در پردازش بخش 1: {str(e)}")
                    
                    # ========================================
                    # بخش 2: تجزیه تحلیل گزارش
                    # ========================================
                    try:
                        part2 = report.get("بخش۲_تجزیه_تحلیل_گزارش", {})
                        
                        # شیت: بند اظهارنظر
                        if "بند_اظهارنظر" in part2:
                            df_opinion = pd.DataFrame([part2["بند_اظهارنظر"]])
                            # ✅ اضافه کردن ستون year
                            df_opinion['year'] = year
                            df_opinion.to_excel(writer, sheet_name="بند_اظهارنظر", index=False)
                        
                        # شیت: بند مبانی اظهارنظر
                        if "بند_مبانی_اظهارنظر" in part2:
                            basis_data = part2["بند_مبانی_اظهارنظر"]
                            if basis_data.get("موضوعیت_دارد", False) and "موارد_مطرح_شده" in basis_data:
                                df_basis = pd.DataFrame(basis_data["موارد_مطرح_شده"])
                                df_basis = flatten_reference_data(df_basis)
                                df_basis = flatten_array_fields(df_basis)
                                # ✅ اضافه کردن ستون year
                                df_basis['year'] = year
                            else:
                                df_basis = pd.DataFrame([{"موضوعیت_دارد": False, "year": year}])
                            df_basis.to_excel(writer, sheet_name="بند_مبانی_اظهارنظر", index=False)
                        
                        # شیت: بند تاکید بر مطالب خاص
                        if "بند_تاکید_بر_مطالب_خاص" in part2:
                            emphasis_data = part2["بند_تاکید_بر_مطالب_خاص"]
                            if emphasis_data.get("موضوعیت_دارد", False) and "موارد_مطرح_شده" in emphasis_data:
                                df_emphasis = pd.DataFrame(emphasis_data["موارد_مطرح_شده"])
                                df_emphasis = flatten_reference_data(df_emphasis)
                                df_emphasis = flatten_array_fields(df_emphasis)
                                # ✅ اضافه کردن ستون year
                                df_emphasis['year'] = year
                                # ✅ اضافه کردن flag موضوعیت
                                df_emphasis['موضوعیت_دارد'] = True
                            else:
                                df_emphasis = pd.DataFrame([{"موضوعیت_دارد": False, "year": year}])
                            df_emphasis.to_excel(writer, sheet_name="بند_تاکید_بر_مطالب_خاص", index=False)
                        
                        # شیت: گزارش رعایت الزامات قانونی
                        if "گزارش_رعایت_الزامات_قانونی" in part2:
                            legal_data = part2["گزارش_رعایت_الزامات_قانونی"]
                            if legal_data.get("موضوعیت_دارد", False) and "تخلفات" in legal_data:
                                violations = legal_data["تخلفات"]
                                processed_violations = []
                                
                                for violation in violations:
                                    processed_violation = violation.copy()
                                    # تبدیل لیست به رشته
                                    if "مبانی_قانونی_و_استانداردها" in processed_violation:
                                        processed_violation["مبانی_قانونی_و_استانداردها"] = ", ".join(
                                            processed_violation["مبانی_قانونی_و_استانداردها"]
                                        )
                                    processed_violations.append(processed_violation)
                                
                                df_legal = pd.DataFrame(processed_violations)
                                df_legal = flatten_reference_data(df_legal)
                                df_legal = flatten_array_fields(df_legal)
                                # ✅ اضافه کردن ستون year
                                df_legal['year'] = year
                                # ✅ اضافه کردن flag موضوعیت
                                df_legal['موضوعیت_دارد'] = True
                            else:
                                df_legal = pd.DataFrame([{"موضوعیت_دارد": False, "year": year}])
                            df_legal.to_excel(writer, sheet_name="گزارش_قانونی", index=False)
                    
                    except Exception as e:
                        logger.warning(f"خطا در پردازش بخش 2: {str(e)}")
                    
                    # ========================================
                    # بخش 3: چک لیست موضوعی
                    # ========================================
                    try:
                        if "بخش۳_چک_لیست_موضوعی" in report:
                            part3 = report["بخش۳_چک_لیست_موضوعی"]
                            df3 = pd.DataFrame(part3)
                            df3 = flatten_reference_data(df3)
                            df3 = flatten_array_fields(df3)
                            # ✅ اضافه کردن ستون year
                            df3['year'] = year
                            df3.to_excel(writer, sheet_name="بخش3_چک_لیست", index=False)
                    except Exception as e:
                        logger.warning(f"خطا در پردازش بخش 3: {str(e)}")
                
                excel_files.append(output_file)
                logger.info(f"Successfully created Excel file: {excel_filename}")
                
            except Exception as e:
                logger.error(f"خطا در ایجاد Excel برای {filename}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
        
        return excel_files

    def create_results_section(results):
        if not results:
            return
        st.subheader("📁 نتایج تفصیلی تحلیل")
        st.divider()
        for filename, result in results:
            with st.container():
                if 'error' in result:
                    st.markdown(f'<div class="new-result-card error-card"><div class="card-header"><h5>{filename} <span class="status-icon error">✗</span></h5></div><p class="error-message">خطا در پردازش فایل: {result["error"]}</p></div>', unsafe_allow_html=True)
                    continue
                try:
                    analysis = result['تحلیل_جامع_گزارش_حسابرسی']['بخش۱_خلاصه_و_اطلاعات_کلیدی']
                    company_name = analysis.get('نام_شرکت', 'N/A')
                    auditor_name = analysis.get('نام_حسابرس', 'N/A')
                    opinion_type = analysis.get('نوع_اظهارنظر', 'N/A')
                    risk_level = analysis.get('سطح_ریسک_کلی_بنا_به_گزارش', 'N/A')
                    financial_year = analysis.get('دوره_مالی', 'N/A')
                    risk_class = get_risk_class(risk_level)
                    st.markdown(f'<div class="new-result-card"><div class="card-header"><h5>{filename} <span class="status-icon success">✓</span></h5></div><div class="new-card-grid"><div class="new-info-box"><div class="new-info-label">🏢 شرکت</div><div class="new-info-value">{company_name}</div></div><div class="new-info-box"><div class="new-info-label">📅 دوره مالی</div><div class="new-info-value">{financial_year}</div></div><div class="new-info-box"><div class="new-info-label">👨‍💼 حسابرس</div><div class="new-info-value">{auditor_name}</div></div><div class="new-info-box"><div class="new-info-label">📋 اظهارنظر</div><div class="new-info-value">{opinion_type}</div></div><div class="new-info-box new-risk-box {risk_class}"><div class="new-info-label">⚠️ سطح ریسک</div><div class="new-info-value">{risk_level}</div></div></div></div>', unsafe_allow_html=True)
                except Exception as e:
                    st.warning(f"⚠️ خطایی در نمایش نتایج فایل {filename} رخ داد: {e}")
        st.markdown("---")
        st.subheader("📥 دانلود خروجی‌ها")
        if 'show_download_options' not in st.session_state:
            st.session_state.show_download_options = False
        if 'show_individual_files' not in st.session_state:
            st.session_state.show_individual_files = False
        if st.button("📂 نمایش گزینه‌های دانلود", type="primary", key="show_downloads_main"):
            st.session_state.show_download_options = not st.session_state.show_download_options
            st.session_state.show_individual_files = False
        if st.session_state.show_download_options:
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔎  دانلود فایل های اکسل", use_container_width=True, key="toggle_individual_files"):
                    st.session_state.show_individual_files = not st.session_state.show_individual_files
            with col2:
                try:
                    excel_files_zip = convert_to_excel(results)
                    if excel_files_zip:
                        zip_buffer = BytesIO()
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for f_path in excel_files_zip:
                                zf.write(f_path, os.path.basename(f_path))
                        zip_buffer.seek(0)
                        st.download_button(label="📦 دانلود همه فایل‌ها (ZIP)", data=zip_buffer.getvalue(), file_name=f"Financial_Analysis_All_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip", use_container_width=True, key="download_zip_final")
                except Exception as e:
                    st.error(f"خطا در آماده‌سازی فایل ZIP: {e}")
            if st.session_state.show_individual_files:
                st.markdown("---")
                st.markdown("#### لیست فایل‌های اکسل برای دانلود:")
                try:
                    excel_files_individual = convert_to_excel(results)
                    if not excel_files_individual:
                        st.warning("هیچ فایل اکسلی برای دانلود تولید نشد.")
                    else:
                        for index, excel_file in enumerate(excel_files_individual):
                            filename = os.path.basename(excel_file)
                            with open(excel_file, 'rb') as f:
                                file_data = f.read()
                            st.download_button(label=f"📄 {filename}", data=file_data, file_name=filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"download_file_{index}", use_container_width=True)
                except Exception as e:
                    st.error(f"خطا در آماده‌سازی فایل‌های تکی: {e}")

    def create_stats_section(results):
        if not results:
            st.info("داده‌ای برای نمایش آمار وجود ندارد.")
            return
        st.markdown('<div class="modern-card">', unsafe_allow_html=True)
        st.subheader("📈 آمار تحلیل")
        st.divider()
        successful = sum(1 for _, r in results if 'error' not in r)
        failed = len(results) - successful
        risk_counts = {'پایین': 0, 'متوسط': 0, 'بالا': 0, 'بحرانی': 0}
        opinion_types = {'مقبول': 0, 'مشروط': 0, 'مردود': 0, 'عدم اظهارنظر': 0}
        company_data = []
        for filename, result in results:
            if 'error' not in result:
                try:
                    analysis = result['تحلیل_جامع_گزارش_حسابرسی']['بخش۱_خلاصه_و_اطلاعات_کلیدی']
                    risk = analysis['سطح_ریسک_کلی_بنا_به_گزارش']
                    if risk in risk_counts: 
                        risk_counts[risk] += 1
                    opinion = analysis['نوع_اظهارنظر']
                    if opinion in opinion_types:
                        opinion_types[opinion] += 1
                    company_data.append({'نام_شرکت': analysis['نام_شرکت'], 'دوره_مالی': analysis['دوره_مالی'], 'حسابرس': analysis.get('نام_حسابرس', 'نامشخص'), 'نوع_اظهارنظر': opinion, 'سطح_ریسک': risk})
                except KeyError: 
                    pass
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"""
                <div class="metric-modern metric-success">
                    <p>تحلیل موفق</p>
                    <div class="metric-value-box">
                        <div class="metric-value">{successful}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
                <div class="metric-modern metric-failed">
                    <p>ناموفق</p>
                    <div class="metric-value-box">
                        <div class="metric-value">{failed}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
        with col3:
            st.markdown(f"""
                <div class="metric-modern metric-highrisk">
                    <p>ریسک بالا / بحرانی</p>
                    <div class="metric-value-box">
                        <div class="metric-value">{risk_counts["بالا"] + risk_counts["بحرانی"]}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
        with col4:
            st.markdown(f"""
                <div class="metric-modern metric-lowrisk">
                    <p>ریسک پایین</p>
                    <div class="metric-value-box">
                        <div class="metric-value">{risk_counts["پایین"]}</div>
                    </div>
                </div>
            """, unsafe_allow_html=True)

        if company_data:
            # <<< بخش توزیع سطح ریسک با ساختار HTML جدید >>>
            st.markdown("<br><h3>📊 توزیع سطح ریسک</h3>", unsafe_allow_html=True)
            risk_icons = {'پایین': '🟢', 'متوسط': '🟡', 'بالا': '🟠', 'بحرانی': '🔴'}
            
            col1, col2, col3, col4 = st.columns(4)
            # ترتیب را مطابق تصویر هدف تغییر می‌دهیم (از سبز به قرمز)
            risk_types = ['پایین', 'متوسط', 'بالا', 'بحرانی']
            columns = [col1, col2, col3, col4]
            
            for col, risk_type in zip(columns, risk_types):
                with col:
                    st.markdown(f'''
                        <div class="stat-card-v2 {risk_type.lower()}">
                            <div class="card-icon-v2">{risk_icons[risk_type]}</div>
                            <div class="card-number-v2">{risk_counts[risk_type]}</div>
                            <div class="card-title-v2">ریسک {risk_type}</div>
                        </div>
                    ''', unsafe_allow_html=True)

            # <<< بخش توزیع نوع اظهارنظر با ساختار HTML جدید >>>
            st.markdown("<br><h3>📊 توزیع نوع اظهارنظر</h3>", unsafe_allow_html=True)
            opinion_icons = {'مقبول': '✅', 'مشروط': '⚠️', 'مردود': '❌', 'عدم اظهارنظر': '⭕'}
            
            col1, col2, col3, col4 = st.columns(4)
            opinion_keys = ['مقبول', 'مشروط', 'مردود', 'عدم اظهارنظر']
            columns = [col1, col2, col3, col4]
            
            for col, op_type in zip(columns, opinion_keys):
                with col:
                    st.markdown(f'''
                        <div class="stat-card-v2 {op_type.lower().replace(' ', '-')}">
                            <div class="card-icon-v2">{opinion_icons[op_type]}</div>
                            <div class="card-number-v2">{opinion_types[op_type]}</div>
                            <div class="card-title-v2">{op_type}</div>
                        </div>
                    ''', unsafe_allow_html=True)

                
            st.markdown("<br><h3>🏢 جدول خلاصه شرکت‌ها</h3>", unsafe_allow_html=True)
            html_table = '<table class="summary-table"><thead><tr><th>شرکت</th><th>دوره مالی</th><th>حسابرس</th><th>اظهارنظر</th><th>سطح ریسک</th></tr></thead><tbody>'
            for i, row in enumerate(company_data):
                risk_class = ""
                if row['سطح_ریسک'] == 'بحرانی': risk_class = "risk-critical"
                elif row['سطح_ریسک'] == 'بالا': risk_class = "risk-high"
                elif row['سطح_ریسک'] == 'متوسط': risk_class = "risk-medium"
                elif row['سطح_ریسک'] == 'پایین': risk_class = "risk-low"
                opinion_class = ""
                if row['نوع_اظهارنظر'] == 'مقبول': opinion_class = "opinion-accepted"
                elif row['نوع_اظهارنظر'] == 'مشروط': opinion_class = "opinion-conditional"
                elif row['نوع_اظهارنظر'] == 'مردود': opinion_class = "opinion-rejected"
                elif row['نوع_اظهارنظر'] == 'عدم اظهارنظر': opinion_class = "opinion-no"
                html_table += f'<tr><td>{row["نام_شرکت"]}</td><td>{row["دوره_مالی"]}</td><td>{row["حسابرس"]}</td><td class="opinion-cell {opinion_class}">{row["نوع_اظهارنظر"]}</td><td class="risk-cell {risk_class}">{row["سطح_ریسک"]}</td></tr>'
            html_table += '</tbody></table>'
            st.markdown(html_table, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


    # ========================================================================
# بخش 9: توابع UI (اصلاح نهایی برای آماده‌سازی و رسم نمودار)
# ========================================================================

    def normalize_company_name(name: str) -> str:
        """
        نرمال‌سازی نام شرکت برای تشخیص یکسان بودن بین سال‌ها
        - حذف تفاوت‌های جزئی در نام‌نویسی
        - یکسان‌سازی حروف فارسی و عربی
        - حذف کلمات و علائم اضافی
        """
        if not name:
            return ""
        
        # حذف فضاهای ابتدا و انتها
        name = name.strip()
        
        # یکسان‌سازی حروف فارسی و عربی
        name = name.replace("ي", "ی").replace("ك", "ک")
        name = name.replace("ة", "ه").replace("ؤ", "و").replace("إ", "ا").replace("أ", "ا")
        
        # تبدیل به حروف کوچک (برای زبان انگلیسی)
        name = name.lower()
        
        # حذف تمام علائم نگارشی و پرانتزها
        name = re.sub(r'[()\[\]{}،,.:;""\'`~!@#$%^&*_+=|\\/<>?]', '', name)
        
        # حذف کلمات و عبارات رایج (case-insensitive)
        patterns_to_remove = [
            r'(?i)شرکت\s*',
            r'(?i)سهامی\s*عام\s*',
            r'(?i)سهامی\s*خاص\s*',
            r'(?i)با\s*مسئولیت\s*محدود\s*',
            r'(?i)عام\s*',
            r'(?i)خاص\s*',
        ]
        
        for pattern in patterns_to_remove:
            name = re.sub(pattern, '', name)
        
        # حذف فاصله‌های اضافی و تبدیل چند فاصله به یک فاصله
        name = re.sub(r'\s+', ' ', name)
        
        # حذف فضاهای ابتدا و انتها مجدد
        name = name.strip()
        
        return name


    def process_and_prepare_dataframes(results: List) -> (Dict, bool):
        """
        این تابع نتایج JSON را مستقیماً پردازش کرده، ستون 'year' را به هر شیت اضافه می‌کند
        و داده‌ها را برای ادغام آماده می‌کند.
        """
        all_dataframes = {}
        company_names = {}  # تغییر از set به dict برای نگهداری نام اصلی و نرمال شده
        
        # 1. پردازش هر فایل به صورت مجزا
        for filename, data in results:
            if "error" in data:
                continue
                
            try:
                report = data["تحلیل_جامع_گزارش_حسابرسی"]
                summary_data = report["بخش۱_خلاصه_و_اطلاعات_کلیدی"]
                
                # استخراج سال و نام شرکت
                company_name = summary_data.get("نام_شرکت", f"Unknown_{filename}")
                normalized_name = normalize_company_name(company_name)
                
                financial_year = summary_data.get("دوره_مالی", "")
                year_match = re.search(r'(\d{4})', financial_year)
                year = int(year_match.group(1)) if year_match else None
                
                if year is None:
                    logger.warning(f"سال مالی برای فایل {filename} یافت نشد. از این فایل در نمودارها صرف‌نظر می‌شود.")
                    continue

                # ذخیره نام نرمال شده با نام اصلی
                if normalized_name not in company_names:
                    company_names[normalized_name] = company_name

                # 2. ساخت دیتافریم برای هر شیت و اضافه کردن ستون 'year'
                # شیت خلاصه
                if 'df_summary' not in all_dataframes: 
                    all_dataframes['df_summary'] = []
                df1 = pd.DataFrame([summary_data])
                df1['year'] = year
                all_dataframes['df_summary'].append(df1)
                
                # شیت تاکید بر مطالب خاص
                part2 = report.get("بخش۲_تجزیه_تحلیل_گزارش", {})
                if "بند_تاکید_بر_مطالب_خاص" in part2:
                    if 'df_emphasis' not in all_dataframes: 
                        all_dataframes['df_emphasis'] = []
                    emphasis_data = part2["بند_تاکید_بر_مطالب_خاص"]
                    if emphasis_data.get("موضوعیت_دارد", False) and "موارد_مطرح_شده" in emphasis_data:
                        df_emphasis = pd.DataFrame(emphasis_data["موارد_مطرح_شده"])
                        df_emphasis['year'] = year
                        df_emphasis['موضوعیت_دارد'] = True
                        all_dataframes['df_emphasis'].append(df_emphasis)
                    else:
                        all_dataframes['df_emphasis'].append(pd.DataFrame([{'موضوعیت_دارد': False, 'year': year}]))

                # شیت تخلفات قانونی
                if "گزارش_رعایت_الزامات_قانونی" in part2:
                    if 'df_violations' not in all_dataframes: 
                        all_dataframes['df_violations'] = []
                    legal_data = part2["گزارش_رعایت_الزامات_قانونی"]
                    if legal_data.get("موضوعیت_دارد", False) and "تخلفات" in legal_data:
                        df_legal = pd.DataFrame(legal_data["تخلفات"])
                        df_legal['year'] = year
                        df_legal['موضوعیت_دارد'] = True
                        all_dataframes['df_violations'].append(df_legal)
                    else:
                        all_dataframes['df_violations'].append(pd.DataFrame([{'موضوعیت_دارد': False, 'year': year}]))
                
                # شیت چک‌لیست
                if "بخش۳_چک_لیست_موضوعی" in report:
                    if 'df_checklist' not in all_dataframes: 
                        all_dataframes['df_checklist'] = []
                    df3 = pd.DataFrame(report["بخش۳_چک_لیست_موضوعی"])
                    df3['year'] = year
                    all_dataframes['df_checklist'].append(df3)

            except Exception as e:
                logger.error(f"خطا در آماده‌سازی دیتافریم برای فایل {filename}: {e}")
                continue

        # 3. اعتبارسنجی نام شرکت (با نام نرمال شده)
        if len(company_names) > 1:
            original_names = list(company_names.values())
            st.warning(f"⚠️ تحلیل روند امکان‌پذیر نیست. فایل‌ها متعلق به شرکت‌های مختلفی هستند: {', '.join(original_names)}")
            return {}, False

        # 4. ادغام نهایی دیتافریم‌ها
        merged_data = {}
        for key, df_list in all_dataframes.items():
            if df_list:
                merged_data[key] = pd.concat(df_list, ignore_index=True)

        return merged_data, True


# بخش اصلاح‌شده برای نمودارها با عناوین markdown و expander توضیحات

# بخش اصلاح‌شده برای نمودارها با عناوین markdown و expander توضیحات

    def create_charts_section(results):
        if not results or not any('error' not in r for _, r in results):
            st.info("داده معتبری برای نمایش نمودارها وجود ندارد.")
            return

        st.markdown('<div class="modern-card">', unsafe_allow_html=True)
        st.subheader("📈 نمودارها و تحلیل روند")
        st.divider()

        try:
            font = setup_persian_font()
            
            # آماده‌سازی و ادغام داده‌ها
            merged_data, is_consistent = process_and_prepare_dataframes(results)
            
            if not is_consistent or not merged_data:
                st.markdown("</div>", unsafe_allow_html=True)
                return

            df_summary = merged_data.get("df_summary")
            df_emphasis = merged_data.get("df_emphasis")
            df_checklist = merged_data.get("df_checklist")
            df_violations = merged_data.get("df_violations")

            # ====================================================================
            # بخش ۱: روندهای کلان
            # ====================================================================
            st.markdown('''
                <h2 style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                        padding: 2rem; 
                        border-radius: 20px; 
                        margin: 2.5rem 0; 
                        box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3);
                        color: white; 
                        text-align: center; 
                        font-size: 1.8rem; 
                        font-weight: 700; 
                        letter-spacing: 1px;">
                    📊 بخش ۱: تحلیل روندهای کلان حسابرسی
                </h2>
                ''', unsafe_allow_html=True)
       
            
            col1, col2 = st.columns(2, gap="large")
            
            # نمودار ۱: روند سطح ریسک
            with col1:
                if df_summary is not None and not df_summary.empty:
                    st.markdown('''
                        <h3 style="background: rgba(240, 147, 251, 0.3); 
                                padding: 0.8rem 1.5rem; 
                                border-radius: 12px; 
                                margin: 0.5rem 0 0 0;
                                color: #2c3e50; 
                                text-align: center; 
                                font-size: 1.2rem; 
                                font-weight: 600;">
                            ⚠️ روند سطح ریسک کلی
                        </h3>
                    ''', unsafe_allow_html=True)
                    
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    fig1 = plot_risk_trend(df_summary, font)
                    st.pyplot(fig1)
                    plt.close(fig1)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    with st.expander("📖 توضیحات نمودار روند ریسک"):
                        st.markdown("""
                        <div style="text-align: right; direction: rtl; padding: 1rem;">
                        
                        **این نمودار چه چیزی را نشان می‌دهد؟**
                        
                        - **محور عمودی**: سطح ریسک (پایین، متوسط، بالا، بحرانی)
                        - **محور افقی**: سال‌های مالی مورد بررسی
                        - **خط روند**: تغییرات سطح ریسک کلی در طول زمان
                        
                        **نکات کلیدی:**
                        - افزایش روند به سمت بالا نشان‌دهنده افزایش ریسک است
                        - کاهش روند نشان‌دهنده بهبود وضعیت کنترل‌های داخلی و کاهش ریسک
                        - ثبات در یک سطح نشان‌دهنده وضعیت پایدار سازمان است
                        
                        </div>
                        """, unsafe_allow_html=True)
            
            # نمودار ۲: روند اظهارنظر
            with col2:
                if df_summary is not None and not df_summary.empty:
                    st.markdown('''
                        <h3 style="background: rgba(250, 112, 154, 0.3); 
                                padding: 0.8rem 1.5rem; 
                                border-radius: 12px; 
                                margin: 0.5rem 0 0 0;
                                color: #2c3e50; 
                                text-align: center; 
                                font-size: 1.2rem; 
                                font-weight: 600;">
                            ✅ روند اظهارنظر حسابرس
                        </h3>
                    ''', unsafe_allow_html=True)
                    
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    fig2 = plot_opinion_trend(df_summary, font)
                    st.pyplot(fig2)
                    plt.close(fig2)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    with st.expander("📖 توضیحات نمودار روند اظهارنظر"):
                        st.markdown("""
                        <div style="text-align: right; direction: rtl; padding: 1rem;">
                        
                        **این نمودار چه چیزی را نشان می‌دهد؟**
                        
                        - **محور عمودی**: نوع اظهارنظر (مقبول، مشروط، مردود، عدم اظهارنظر)
                        - **محور افقی**: سال‌های مالی
                        - **خط روند**: تغییرات نوع نظر حسابرس در طول زمان
                        
                        **تفسیر:**
                        - **مقبول**: صورت‌های مالی عاری از تحریف با اهمیت
                        - **مشروط**: وجود محدودیت یا انحراف قابل توجه
                        - **مردود**: عدم انطباق با استانداردها
                        - **عدم اظهارنظر**: عدم امکان رسیدگی کافی
                        
                        </div>
                        """, unsafe_allow_html=True)

            # ====================================================================
            # بخش ۲: نقشه حرارتی
            # ====================================================================
            st.markdown('''
                <h2 style="background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%); 
                        padding: 2rem; 
                        border-radius: 20px; 
                        margin: 2.5rem 0; 
                        box-shadow: 0 10px 30px rgba(168, 237, 234, 0.3);
                        color: #2c3e50; 
                        text-align: center; 
                        font-size: 1.8rem; 
                        font-weight: 700; 
                        letter-spacing: 1px;">
                    🔥 بخش ۲: نقشه حرارتی موضوعات کلیدی حسابرسی
                </h2>
            ''', unsafe_allow_html=True)
            
            if df_checklist is not None and not df_checklist.empty:
                st.markdown('''
                    <h3 style="background: rgba(255, 154, 158, 0.3); 
                            padding: 0.8rem 1.5rem; 
                            border-radius: 12px; 
                            margin: 0.5rem 0 0 0;
                            color: #2c3e50; 
                            text-align: center; 
                            font-size: 1.2rem; 
                            font-weight: 600;">
                        🎯 نقشه حرارتی وضعیت موضوعات کلیدی
                    </h3>
                ''', unsafe_allow_html=True)
                
                st.markdown('<div class="chart-container-full">', unsafe_allow_html=True)
                fig3 = plot_checklist_heatmap(df_checklist, font)
                st.pyplot(fig3)
                plt.close(fig3)
                st.markdown('</div>', unsafe_allow_html=True)
                
                with st.expander("📖 توضیحات نقشه حرارتی"):
                    st.markdown("""
                    <div style="text-align: right; direction: rtl; padding: 1rem;">
                    
                    **این نقشه چه چیزی را نشان می‌دهد؟**
                    
                    - **ردیف‌ها**: موضوعات کلیدی مورد بررسی (کفایت سرمایه، تسعیر ارز، مالیات و...)
                    - **ستون‌ها**: سال‌های مالی
                    - **رنگ سلول‌ها**: سطح ریسک یا وضعیت هر موضوع
        
                    **کاربرد:**
                    - شناسایی موضوعات تکراری در طول زمان
                    - تشخیص روند بهبود یا بدتر شدن هر موضوع
                    - اولویت‌بندی موضوعات پرریسک
                    
                    </div>
                    """, unsafe_allow_html=True)

            # ====================================================================
            # بخش ۳: ریسک‌های برجسته
            # ====================================================================
            st.markdown('''
                <h2 style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); 
                        padding: 2rem; 
                        border-radius: 20px; 
                        margin: 2.5rem 0; 
                        box-shadow: 0 10px 30px rgba(79, 172, 254, 0.3);
                        color: white; 
                        text-align: center; 
                        font-size: 1.8rem; 
                        font-weight: 700; 
                        letter-spacing: 1px;">
                    ⚠️ بخش ۳: تحلیل ریسک‌های برجسته شده در گزارش
                </h2>
            ''', unsafe_allow_html=True)
            
            if df_emphasis is not None and not df_emphasis.empty and df_emphasis['موضوعیت_دارد'].any():
                col3, col4 = st.columns([1, 1], gap="large")
                
                # نمودار ۴: ستونی انباشته ریسک‌ها
                with col3:
                    st.markdown('''
                        <h3 style="background: rgba(255, 183, 197, 0.3); 
                                padding: 0.8rem 1.5rem; 
                                border-radius: 12px; 
                                margin: 0.5rem 0 0 0;
                                color: #2c3e50; 
                                text-align: center; 
                                font-size: 1.2rem; 
                                font-weight: 600;">
                            📊 روند تعداد و ترکیب ریسک‌ها
                        </h3>
                    ''', unsafe_allow_html=True)
                    
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    fig4 = plot_risk_stacked_bar(df_emphasis, font)
                    st.pyplot(fig4)
                    plt.close(fig4)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    with st.expander("📖 توضیحات نمودار ستونی ریسک‌ها"):
                        st.markdown("""
                        <div style="text-align: right; direction: rtl; padding: 1rem;">
                        
                        **این نمودار چه چیزی را نشان می‌دهد؟**
                        
                        - **ستون‌ها**: تعداد کل ریسک‌های برجسته شده در هر سال
                        - **رنگ‌های مختلف**: دسته‌های اصلی ریسک (اعتباری، بازار، عملیاتی و...)
                        - **عدد روی هر بخش**: تعداد موارد آن دسته ریسک
                        
                        **تحلیل:**
                        - افزایش ارتفاع ستون = افزایش تعداد کل ریسک‌ها
                        - تغییر ترکیب رنگ‌ها = تغییر در نوع ریسک‌های غالب
                        - غلبه یک رنگ = تمرکز ریسک در یک دسته خاص
                        
                        **هشدار:** افزایش ناگهانی در یک دسته ریسک خاص نیاز به توجه ویژه دارد.
                        
                        </div>
                        """, unsafe_allow_html=True)
                
                # نمودار ۵: Sunburst ریسک‌ها
                with col4:
                    st.markdown('''
                        <h3 style="background: rgba(210, 153, 194, 0.3); 
                                padding: 0.8rem 1.5rem; 
                                border-radius: 12px; 
                                margin: 0.5rem 0 0 0;
                                color: #2c3e50; 
                                text-align: center; 
                                font-size: 1.2rem; 
                                font-weight: 600;">
                            🎯 توزیع سلسله‌مراتبی ریسک‌ها
                        </h3>
                    ''', unsafe_allow_html=True)
                    
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    fig5 = plot_risk_sunburst(df_emphasis)
                    st.plotly_chart(fig5, use_container_width=True)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    with st.expander("📖 توضیحات نمودار سلسله مراتبی ریسک‌ها"):
                        st.markdown("""
                        <div style="text-align: right; direction: rtl; padding: 1rem;">
                        
                        **این نمودار چه چیزی را نشان می‌دهد؟**
                        
                        - **مرکز دایره**: کل ریسک‌های برجسته شده
                        - **حلقه داخلی**: دسته‌های اصلی ریسک
                        - **حلقه خارجی**: زیرشاخه‌های هر دسته اصلی
                        
                        **نحوه استفاده:**
                        - اندازه هر قطاع متناسب با تعداد موارد آن است
                        - کلیک روی هر قسمت برای بزرگ‌نمایی
                        - Hover برای مشاهده جزئیات (نام، تعداد، درصد)
                        
                        **مزایا:**
                        - دید کلی از توزیع ریسک‌ها در یک نگاه
                        - شناسایی سریع پرتکرارترین دسته‌های ریسک
                        - درک رابطه بین دسته اصلی و زیرشاخه‌ها
                        
                        </div>
                        """, unsafe_allow_html=True)
            else:
                st.info("📌 ریسک برجسته‌ای در این گزارش‌ها ذکر نشده است.")

            # ====================================================================
            # بخش ۴: تخلفات قانونی
            # ====================================================================
            st.markdown('''
                <h2 style="background: linear-gradient(135deg, #30cfd0 0%, #330867 100%); 
                        padding: 2rem; 
                        border-radius: 20px; 
                        margin: 2.5rem 0; 
                        box-shadow: 0 10px 30px rgba(48, 207, 208, 0.3);
                        color: white; 
                        text-align: center; 
                        font-size: 1.8rem; 
                        font-weight: 700; 
                        letter-spacing: 1px;">
                    ⚖️ بخش ۴: تحلیل تخلفات و الزامات قانونی
                </h2>
            ''', unsafe_allow_html=True)
            
            if df_violations is not None and not df_violations.empty and df_violations['موضوعیت_دارد'].any():
                col5, col6 = st.columns([1, 1], gap="large")
                
                # نمودار ۶: ستونی انباشته تخلفات
                with col5:
                    st.markdown('''
                        <h3 style="background: rgba(252, 74, 26, 0.3); 
                                padding: 0.8rem 1.5rem; 
                                border-radius: 12px; 
                                margin: 0.5rem 0 0 0;
                                color: #2c3e50; 
                                text-align: center; 
                                font-size: 1.2rem; 
                                font-weight: 600;">
                            📉 روند تعداد و ترکیب تخلفات
                        </h3>
                    ''', unsafe_allow_html=True)
                    
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    fig6 = plot_violations_stacked_bar(df_violations, font)
                    st.pyplot(fig6)
                    plt.close(fig6)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    with st.expander("📖 توضیحات نمودار تخلفات قانونی"):
                        st.markdown("""
                        <div style="text-align: right; direction: rtl; padding: 1rem;">
                                
                        **این نمودار چه چیزی را نشان می‌دهد؟**
                        
                        - **ستون‌ها**: تعداد کل موارد عدم رعایت الزامات قانونی
                        - **رنگ‌ها**: دسته‌بندی تخلفات (نهاد ناظر، بازار سرمایه، حاکمیتی، مالیاتی و...)
                        - **اعداد**: تعداد موارد تخلف در هر دسته
                        
                        **اهمیت:**
                        - افزایش تخلفات = ضعف در رعایت الزامات قانونی
                        - کاهش تخلفات = بهبود سیستم‌های کنترلی و حاکمیتی
                        - تکرار تخلف در یک دسته = نیاز به اقدام اصلاحی اساسی
                        
                        **توجه:** تخلفات قانونی می‌تواند منجر به جریمه‌های مالی و آسیب به شهرت سازمان شود.
                                    
                        </div>
                        """,unsafe_allow_html=True)
                
                # نمودار ۷: Sunburst تخلفات
                with col6:
                    st.markdown('''
                        <h3 style="background: rgba(238, 156, 167, 0.3); 
                                padding: 0.8rem 1.5rem; 
                                border-radius: 12px; 
                                margin: 0.5rem 0 0 0;
                                color: #2c3e50; 
                                text-align: center; 
                                font-size: 1.2rem; 
                                font-weight: 600;">
                            🔍 ساختار و توزیع تخلفات
                        </h3>
                    ''', unsafe_allow_html=True)
                    
                    st.markdown('<div class="chart-container">', unsafe_allow_html=True)
                    fig7 = plot_violations_sunburst(df_violations)
                    st.plotly_chart(fig7, use_container_width=True)
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    with st.expander("📖 توضیحات نمودار سلسله مراتبی تخلفات"):
                        st.markdown("""
                            <div style="text-align: right; direction: rtl; padding: 1rem;">
                                    
                        **این نمودار چه چیزی را نشان می‌دهد؟**
                        
                        - **حلقه مرکزی**: کل تخلفات
                        - **حلقه میانی**: دسته‌های اصلی قوانین (الزامات بانک مرکزی، بورس، حاکمیتی و...)
                        - **حلقه خارجی**: جزئیات تخلف در هر دسته
                        
                        **کاربردها:**
                        - شناسایی پرتکرارترین نوع تخلف
                        - درک رابطه بین دسته اصلی قانون و موارد تخلف
                        - اولویت‌بندی برای اقدامات اصلاحی
                        
                        **نکته مهم:** 
                        تمرکز تخلفات در یک دسته خاص (مثلاً الزامات بانک مرکزی) 
                        نشان‌دهنده نیاز به توجه ویژه به آن حوزه است.
                                    
                        </div>
                        """,unsafe_allow_html=True)
            else:
                st.info("✅ در این گزارش‌ها، تخلف قانونی گزارش نشده است.")
                
        except Exception as e:
            st.error(f"❌ خطا در رسم نمودارها: {str(e)}")
            logger.error(f"Chart error: {traceback.format_exc()}")
            
        st.markdown("</div>", unsafe_allow_html=True)
    # ========================================================================
    # بخش 10: تابع اصلی main
    # ========================================================================

    def main():
        if 'results' not in st.session_state:
            st.session_state.results = None
        global api_key_manager
        api_key_manager = APIKeyManager(st.session_state.api_keys)
        create_header()
        tab1, tab2, tab3, tab4 = st.tabs(["📤 آپلود و پردازش", "📊 نتایج تحلیل", "📈 اطلاعات آماری", "📉 ترند و نمودارها "])
        with tab1:
            with st.expander("📋 راهنمای بارگذاری فایل", expanded=False):
                st.markdown("""
                <div style="text-align: right; direction: rtl; padding: 1rem; border-radius: 12px;">
                
                #### ✨ مراحل کار:
                
                * 📄 **فایل‌های PDF گزارش حسابرسی** را انتخاب کنید
                * 📦 یا یک **فایل ZIP** حاوی چندین PDF بارگذاری کنید
                * ✅ پس از انتخاب، فایل‌ها را بررسی کنید
                * 🚀 دکمه **"شروع تحلیل"** را بزنید تا پردازش آغاز شود
                
                ---
                
                #### ⚠️ نکات مهم:
                
                * **فرمت پشتیبانی شده:** فقط PDF
                * **حداکثر حجم هر فایل:** 50 مگابایت
                * **کیفیت مهم است:** اسکن‌های واضح نتایج بهتری دارند
                
                ---
                
                #### 🎯 ویژگی‌های بررسی شده:
                
                ✔️ **اطلاعات شرکت** - نام، دوره مالی  
                ✔️ **اظهارنظر حسابرس** - نوع و سطح ریسک  
                ✔️ **ریسک‌های برجسته** - تشخیص و دسته‌بندی  
                ✔️ **تخلفات قانونی** - شناسایی ، تحلیل و دسته بندی  
                ✔️ **موضوعات کلیدی** - شناسایی و تحلیل  
                            
                </div>
                            
                """, unsafe_allow_html=True)
            uploaded_files = create_file_upload_section()
            if uploaded_files:
                results = create_processing_section(uploaded_files)
                if results:
                    st.session_state.results = results
        with tab2:
           
            if st.session_state.results:
                create_results_section(st.session_state.results)
            else:
                st.info("هنوز فایلی پردازش نشده است.")
        with tab3:
            if st.session_state.results:
                create_stats_section(st.session_state.results)
            else:
                st.info("هنوز فایلی پردازش نشده است.")
        with tab4:
            
            with st.expander("📉 راهنمای نمودارها و ترند", expanded=False):
                   
                st.markdown("""
                    <div style="text-align: right; direction: rtl; padding: 1rem; border-radius: 12px;">

                #### **⚠️ توجه :**
                #### این نمودارها در صورتی ترسیم میشود که صورتهای مالی شما متعلق به یک مرجع باشد زیرا هدف مقایسه رفتار یک مرجع در طی چند سال است 
                #### چناچه عنوان صورت های مالی تفاوت های جزئی داشته باشند یکسان سازی عنوان صورت گرفته سپس نمودار رسم میگیردد 
                - * ✅ بانک تجارت (سهامی عام), بانک تجارت (شرکت سهامی عام)
                - * ❌ بانک صادرات , بانک تجارت (شرکت سهامی عام)
                ---
                #### 📖 توضیحات نمودارها:
                
                هر نمودار دارای یک **Expander با آیکون 📖** است که شامل:
                 - * توضیح کامل نمودار
                 - * نحوه استفاده
                 - * اهمیت و کاربرد
                 - * نکات مهم تحلیلی
                ---
                #### 🎯 ویژگی‌های نمودارها:
                
                ✔️ **تعاملی** - Hover برای مشاهده جزئیات  
                ✔️ **رنگ‌بندی هوشمند** - رنگ‌های متمایز برای هر دسته  
                ✔️ **فونت فارسی** - نمایش صحیح متن‌های فارسی  
                ✔️ **توضیحات کامل** - راهنمای هر نمودار در Expander  
                
                ---
                
                #### 💡 نکات استفاده:
                
                - **کلیک و بزرگ‌نمایی** - در نمودارهای Sunburst
                - **Hover برای جزئیات** - روی هر بخش از نمودار
                - **مقایسه روند** - بین دوره‌های مختلف
                - **شناسایی الگو** - در توزیع ریسک‌ها و تخلفات                            

                </div>
                """, unsafe_allow_html=True)
        
            if st.session_state.results:
                create_charts_section(st.session_state.results)
            else:
                st.info("هنوز فایلی پردازش نشده است.")

    if __name__ == "__main__":
        main()
