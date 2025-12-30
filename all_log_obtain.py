import sys
import re
import urllib.request
import ssl
import schedule
from duplicate_removal import duplicate_removal
from typing import List
from bs4 import BeautifulSoup
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os


class Tee:
    """同时输出到控制台和文件的类"""

    def __init__(self, filename):
        self.file = open(filename, 'w', encoding='utf-8')
        self.stdout = sys.stdout
        self.stderr = sys.stderr
        sys.stdout = self
        sys.stderr = self

    def write(self, message):
        # 输出到控制台
        self.stdout.write(message)
        # 写入文件
        self.file.write(message)
        # 立即刷新缓冲区
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        # 恢复原始输出流
        sys.stdout = self.stdout
        sys.stderr = self.stderr
        # 关闭文件
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def expand_shadow_dom(driver):
    """递归展开页面中的所有Shadow DOM"""
    driver.execute_script("""
        function expandShadowRoots(root) {
            root.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot) {
                    const container = document.createElement('div');
                    container.className = '__shadow_contents';
                    container.innerHTML = el.shadowRoot.innerHTML;
                    el.appendChild(container);
                    expandShadowRoots(container);
                    }
                });
        }
        // 从 document.body 开始
        expandShadowRoots(document.body);
    """)
    print("🔍 Shadow DOM已展平")


def expand_shadow_dom_with_timeout(driver, timeout=3):
    """递归展开页面中的所有Shadow DOM，但最多执行指定秒数"""
    start_time = time.time()

    # 定义展开函数
    expand_js = """
    function expandShadowRoots(root) {
        const elements = Array.from(root.querySelectorAll('*'));
        let count = 0;

        for (const el of elements) {
            if (el.shadowRoot && !el.shadowRoot.__expanded) {
                const container = document.createElement('div');
                container.className = '__shadow_contents';
                container.innerHTML = el.shadowRoot.innerHTML;
                el.appendChild(container);
                el.shadowRoot.__expanded = true;
                count++;

                // 递归展开新添加的内容
                count += expandShadowRoots(container);
            }
        }
        return count;
    }

    // 从 document.body 开始
    return expandShadowRoots(document.body);
    """

    print(f"⏱️ 开始展平Shadow DOM，最多等待{timeout}秒...")

    # 使用循环逐步展开，而不是一次性执行
    while time.time() - start_time < timeout:
        cnt = driver.execute_script(expand_js)
        if cnt == 0:
            print("✅ Shadow DOM已完全展平")
            return
        time.sleep(0.1)  # 短暂暂停避免过度占用CPU

    print(f"⏱️ 时间到，已展平部分Shadow DOM")


def extract_build_log_urls(chromedriver_path, url, combined, mark):
    """
    从combined列表处理按钮点击并提取日志URL
    修复了 script timeout 报错，并恢复了原始日志打印格式
    """
    log_url_list = []
    date_and_state_list = []

    # 使用 idx_in_loop 确保 mark 数组对应关系正确
    for idx_in_loop in range(len(combined)):
        index, timestamp, status = combined[idx_in_loop]

        if mark[idx_in_loop] == 3:  # 跳过不需要的按钮
            continue

        driver = None
        try:
            # 初始化ChromeDriver
            opts = Options()
            opts.add_argument("--headless")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--no-sandbox")
            opts.add_argument(
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
            )
            service = Service(executable_path=chromedriver_path)
            driver = webdriver.Chrome(service=service, options=opts)

            # --- 关键修复：设置脚本执行超时时间，防止海量日志导致超时 ---
            driver.set_script_timeout(120)

            # 访问URL
            driver.get(url)

            # 等待 build-status 出现
            WebDriverWait(driver, 100).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
            )
            time.sleep(20)

            # 初始展开Shadow DOM
            expand_shadow_dom(driver)

            # 提取日期部分
            date_part = timestamp.split()[0].replace("/", "_")
            status_str = "success" if status == 1 else "error"

            # --- 恢复原始打印格式 ---
            print(f"🖱️ 点击按钮 #{index} ({timestamp}, {status_str})...")

            max_retries = 2
            retry_count = 0
            success = False

            # 重试循环
            while retry_count <= max_retries and not success:
                try:
                    success = driver.execute_script("""
                        const idx = arguments[0];
                        const buildStatus = document.querySelector('body > build-status, body > * > build-status');
                        if (!buildStatus || !buildStatus.shadowRoot) return false;
                        const shadow = buildStatus.shadowRoot;

                        let btn;
                        if (idx === "GREEN") {
                            btn = shadow.querySelector('paper-button.green');
                        } else {
                            const buildHistory = shadow.querySelector('div.buildHistory');
                            const buttons = buildHistory ? buildHistory.querySelectorAll('paper-button') : [];
                            btn = buttons[idx];
                        }

                        if (btn) {
                            btn.click();
                            return true;
                        }
                        return false;
                    """, index)

                    if not success:
                        print(f"⚠️ 无法点击按钮 #{index}")
                        raise Exception("JavaScript点击操作失败")

                    print(f"✅ 按钮 #{index} 已点击 (尝试 {retry_count + 1}/{max_retries + 1})")
                    success = True

                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ 尝试 #{retry_count + 1} 失败: {error_msg}")
                    if "Read timed out" in error_msg and retry_count < max_retries:
                        retry_count += 1
                        print(f"♻️ 将在 {2 ** retry_count} 秒后重试...")
                        time.sleep(2 ** retry_count)
                    else:
                        break

            if not success:
                print(f"⚠️ 无法点击按钮 #{index}，跳过")
                with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                    fi.write(url + "\n")
                continue

            # 等待日志加载
            print("⏳ 等待日志加载...")
            expand_shadow_dom_with_timeout(driver, 3)

            # 获取页面HTML
            page_html = driver.page_source

            # 提取日志文件URL
            log_url = None
            try:
                soup = BeautifulSoup(page_html, 'html.parser')
                log_links = soup.find_all('a', href=True)

                for link in log_links:
                    href = link.get('href', '')
                    if href.startswith('/log-') and href.endswith('.txt'):
                        log_url = f"https://oss-fuzz-build-logs.storage.googleapis.com{href}"
                        # --- 恢复原始打印格式 ---
                        print(f"🔗 找到日志文件URL: {log_url}")
                        log_url_list.append(log_url)
                        date_and_state_list.append(date_part + " " + status_str)
                        break

                if not log_url:
                    print("⚠️ 未找到日志文件URL")
                    with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                        fi.write(url + "\n")

            except Exception as e:
                print(f"❌ 日志URL提取失败: {str(e)}")
                with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                    fi.write(url + "\n")

        except Exception as e:
            print(f"❌ 处理按钮 #{index} 时发生错误: {str(e)}")
            with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
                fi.write(url + "\n")
            continue

        finally:
            if 'driver' in locals() and driver:
                driver.quit()
                # --- 恢复原始打印格式 ---
                print(f"🚪 按钮 #{index} 的浏览器已关闭")

    return log_url_list, date_and_state_list


def fetch_rendered_page(chromedriver_path: str, output_path: str):
    """
    展开所有 shadowRoot，对目标url进行获取
    """
    # 1. Chrome 启动配置
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    # 匹配浏览器请求头，模拟真实 Chrome
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )

    # 2. 指定 chromedriver 可执行文件
    service = Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(service=service, options=opts)

    try:
        driver.get("https://oss-fuzz-build-logs.storage.googleapis.com/index.html")

        # 等待 build-status 出现并异步加载完毕
        WebDriverWait(driver, 100).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
        )
        time.sleep(20)

        # —— 递归展开所有 shadowRoot
        expand_shadow_dom(driver)
        rendered_html = driver.page_source

        # 6. 保存到本地文件
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(rendered_html)
        print(f"✅ 渲染后页面已保存到 {output_path}")

    finally:
        driver.quit()


def extract_between_markers(html: str) -> List[str]:
    """
    使用正则表达式从 html 文本中抽取所有外层 <div>…</div> 结构内的项目名，
    仅在该 <div> 内含有 icon="icons:error" 才匹配。
    对每个匹配结果，去掉可能残留的 '/dom-if>' 前缀，只保留真正的项目名。
    """
    pattern = re.compile(
        r'<iron-icon[^>]*icon=["\']icons:error["\'][\s\S]*?</iron-icon>'  # 包含 error 图标
        r'[\s\S]*?'  # 中间任意内容（shadow DOM、dom-if 等）
        r'([^<\s][^<]+?)\s*'  # 捕获非空白开头直到下一个 '<' 之间的文本
        r'</div>',  # 直到外层 </div>
        re.IGNORECASE
    )
    raw = pattern.findall(html)
    cleaned = []
    for m in raw:
        # m 里可能是 "/dom-if>\n                  zip-rs"
        # split by '>'，取最后一段，再 strip 掉前后空白
        name = m.split('>')[-1].strip()
        cleaned.append(name)
    return cleaned


def fetch_and_extract(chromedriver_path: str) -> List[str]:
    """
    启动 Chrome、展平 Shadow DOM、获取页面 HTML，
    并提取所有项目名称对应的url，最后以列表形式返回。
    """
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )

    service = Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(service=service, options=opts)
    try:
        driver.get("https://oss-fuzz-build-logs.storage.googleapis.com/index.html")
        WebDriverWait(driver, 100).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
        )
        time.sleep(20)

        # 展平所有 Shadow DOM
        expand_shadow_dom(driver)
        # 获取完整渲染后的 HTML
        rendered_html = driver.page_source

        # 提取并返回所有匹配的片段列表
        return extract_between_markers(rendered_html)

    finally:
        driver.quit()


def download_with_urllib(log_url, log_filename, project_name, step):
    """
    将目标 log 下载到本地
    参数依次是日志下载url列表，存储文件名列表，存储文件夹名称，重试次数
    """
    try:
        # 创建自定义上下文，忽略SSL验证
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        # 设置自定义 User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        }

        # 创建请求
        req = urllib.request.Request(log_url, headers=headers)

        print(f"⬇️ 开始下载日志 (urllib): {log_url}")
        with urllib.request.urlopen(req, context=context, timeout=50) as response:
            data = response.read()

            # 构建保存目录：./build_error_log_of_projects/项目名
            base_dir = "build_error_log_of_projects"
            target_dir = os.path.join(base_dir, project_name)

            # 确保保存文件夹存在
            os.makedirs(target_dir, exist_ok=True)

            # 构建完整的文件路径
            full_path = os.path.join(target_dir, log_filename)

            # 保存文件
            with open(full_path, "wb") as log_file:
                log_file.write(data)

            print(f"💾 日志已下载并保存到: {full_path}")
            print(f"📝 日志大小: {len(data)} 字符")
            return True

    except Exception as e:
        print(f"❌ 下载日志文件失败 (urllib): {str(e)}")
        if step < 3:
            print(f"✅ 下载日志文件重试 (urllib): {step + 1}/3")
            download_with_urllib(log_url, log_filename, project_name, step + 1)
        return False


def fetch_rendered_page_and_done(chromedriver_path, url, step):
    """
    增加了绿色按钮检测和全失败兜底逻辑
    """
    project_name = url.split("#")[-1] if "#" in url else "unknown_project"
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--enable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    )
    opts.add_argument("--window-size=1200,900")

    driver = webdriver.Chrome(service=Service(chromedriver_path), options=opts)
    try:
        driver.get(url)
        print(f"🌐 访问URL: {url}")

        WebDriverWait(driver, 100).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "build-status"))
        )
        print("✅ 主组件已加载")
        time.sleep(20)

        expand_shadow_dom(driver)

        # 1. 提取绿色按钮信息 (Last Successful Build)
        green_btn_info = driver.execute_script("""
            const shadow = document.querySelector('build-status').shadowRoot;
            const btn = shadow.querySelector('paper-button.green');
            return btn ? { exists: true, text: btn.textContent.trim() } : { exists: false };
        """)

        # 2. 获取 Build History 按钮
        buttons = driver.find_elements(By.CSS_SELECTOR, "div.buildHistory paper-button")

        ts_pattern = re.compile(r"\d{4}/\d{1,2}/\d{1,2}\s*\d{1,2}:\d{2}:\d{2}")
        timestamps = []
        for btn in buttons:
            m = ts_pattern.search(btn.text)
            timestamps.append(m.group() if m else "unknown_time")

        note = []
        for btn in buttons:
            outer_html = btn.get_attribute("outerHTML")
            if 'icon="icons:done"' in outer_html:
                note.append(1)
            elif 'icon="icons:error"' in outer_html:
                note.append(0)
            else:
                note.append(-1)

        # 3. 逻辑计算：生成 mark 数组 (重复过滤 + 全失败兜底)
        has_success_in_history = (1 in note)
        mark = []
        number = 0
        for i in range(len(note)):
            # 兜底规则：如果历史记录中完全没有成功状态，则第一个按钮 (#0) 强制保留
            if i == 0 and not has_success_in_history:
                mark.append(note[i])
                number += 1
                continue

            # 原始去重逻辑
            if len(note) == 1:
                mark.append(note[i]);
                number += 1
            elif i == 0 and i + 1 < len(note) and note[i] == note[i + 1]:
                mark.append(3)
            elif i == len(note) - 1 and i - 1 >= 0 and note[i] == note[i - 1]:
                mark.append(3)
            elif i - 1 >= 0 and i + 1 < len(note) and note[i] == note[i - 1] and note[i] == note[i + 1]:
                mark.append(3)
            else:
                mark.append(note[i]);
                number += 1

        # 4. 组合数据并注入绿色按钮任务
        combined = [(i, timestamps[i], note[i]) for i in range(len(timestamps))]

        if green_btn_info['exists']:
            m_green = ts_pattern.search(green_btn_info['text'])
            green_ts = m_green.group() if m_green else "unknown_time"
            # 插入到任务队列首位，使用特殊索引 "GREEN"
            combined.insert(0, ("GREEN", green_ts, 1))
            mark.insert(0, 1)  # 强制执行
            number += 1
            print(f"✨ 已捕获最后成功构建时间: {green_ts}")

        print(f"📊 构建状态统计: 成功={note.count(1)}, 失败={note.count(0)}, 未知={note.count(-1)}")
        driver.quit()

        if number != 0:
            with open("target_url_list.txt", "a", encoding="utf-8") as fi:
                fi.write(url + "\n")

            # 执行抓取
            log_url_list, date_and_state_list = extract_build_log_urls(chromedriver_path, url, combined, mark)

            # 下载日志
            for i, log_url in enumerate(log_url_list):
                download_with_urllib(log_url, date_and_state_list[i], project_name, 0)

            print("✅ 所有构建日志处理完成")

        return {
            "project": project_name,
            "total_buttons": len(buttons),
            "processed": number
        }

    except Exception as e:
        print(f"❌ 发生错误: {str(e)}")
        with open("wrong_url_list.txt", "a", encoding="utf-8") as fi:
            fi.write(url + "\n")
        return None
    finally:
        if 'driver' in locals() and driver:
            driver.quit()
        print("🚪 浏览器已关闭")


def run_fuzz_log_task(chromedriver_path):
    """包装 main 函数，使其可以被 schedule 调用，并处理可能的异常。"""
    try:
        print(f"\n" + "=" * 80)
        print(f"🚀 开始执行 Fuzz Log 抓取任务 (当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})...")
        print(f"=" * 80 + "\n")
        main(chromedriver_path)
        print(f"\n" + "=" * 80)
        print(f"✅ Fuzz Log 抓取任务执行完成。")
        print(f"=" * 80 + "\n")
    except Exception as e:
        print(f"\n" + "=" * 80)
        print(f"❌ Fuzz Log 抓取任务执行失败: {e}")
        print(f"=" * 80 + "\n")
        import traceback
        traceback.print_exc()


def main(chromedriver_path):
    """主函数"""
    # 创建日志文件名（包含时间戳）
    run_log_dir = "logs"
    os.makedirs(run_log_dir, exist_ok=True)
    log_filename = os.path.join(run_log_dir, f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

    # 使用Tee类重定向输出
    with Tee(log_filename) as tee:
        try:
            # 在这里调用您的核心功能
            # 获取网页html内容
            output_path = "oss_fuzz_index_with_build_status.html"
            fetch_rendered_page(chromedriver_path, output_path)
            # 获取所有build失败的项目的URL
            snippets_list = fetch_and_extract(chromedriver_path)
            # 获取各个构件失败项目的URL
            print("抽取到的所有项目拼接url：")
            project_urls = []
            base_url = "https://oss-fuzz-build-logs.storage.googleapis.com/index.html#"
            for idx, snippet in enumerate(snippets_list, 1):
                project_urls.append(base_url + snippet)
                print(f"{idx}: {base_url + snippet}\n")
            with open("project_url_list.txt", "w", encoding="utf-8") as f:
                for url in project_urls:
                    f.write(url + "\n")
            print(f"✅ 已将 {len(project_urls)} 条 URL（保存到 project_url_list.txt")
            result = duplicate_removal('target_url_list.txt', 'project_url_list.txt')
            if result > 0:
                print(f"将{result} 个项目 url 追加进 project_url_list.txt")
            # 获取并下载日志到本地
            with open("project_url_list.txt", "r", encoding="utf-8") as fin:
                for line in fin:
                    url = line.strip()
                    if not url:
                        continue
                    result = fetch_rendered_page_and_done(chromedriver_path, url, 0)
                    if result:
                        print(f"🎉 项目 '{result['project']}' 处理完成")
                        print(f"  总按钮数: {result['total_buttons']}")
                        print(f"  处理按钮数: {result['processed']}")
            with open("wrong_url_list.txt", "r", encoding="utf-8") as fin:
                for line in fin:
                    url = line.strip()
                    if not url:
                        continue
                    result = fetch_rendered_page_and_done(chromedriver_path, url, 0)
                    if result:
                        print(f"🎉 项目 '{result['project']}' 处理完成")
                        print(f"  总按钮数: {result['total_buttons']}")
                        print(f"  处理按钮数: {result['processed']}")
            # 清空文件
            with open("wrong_url_list.txt", 'w', encoding='utf-8') as input_file:
                input_file.write('')
        except Exception as e:
            # 捕获并记录所有未处理异常.
            print(f"❌ 发生未处理的异常: {str(e)}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            raise  # 重新抛出异常以便在finally块中处理
        finally:
            # 确保所有输出都被刷新
            tee.flush()
            print("✅ 日志已保存到:", log_filename)


if __name__ == "__main__":

    # 获取当前脚本所在的目录,构建相对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    chromedriver_path = os.path.join(
        current_dir,
        "chromedriver",
        "chromedriver-linux64",
        "chromedriver"
    )

    print(schedule.__file__)  # 检查 schedule 模块
    print(f"配置的ChromeDriver路径: {chromedriver_path}")
    main(chromedriver_path)
    # schedule.every().day.at("01:00").do(run_fuzz_log_task, chromedriver_path)
    # schedule.every().day.at("23:00").do(run_fuzz_log_task, chromedriver_path)

    print("\n" + "#" * 80)
    print("Python Fuzz Log 抓取调度器已启动。")
    print("任务将在每天的本地时间 01:00 和 23:00 自动执行。")
    print("请保持此脚本运行，不要关闭终端。")
    print("#" * 80 + "\n")

    # 循环运行调度器，每分钟检查一次是否有待执行任务
    while True:
        schedule.run_pending()
        time.sleep(60)  # 短暂暂停 60 秒 (1 分钟)，避免CPU占用过高
