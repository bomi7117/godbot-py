import nextcord
from nextcord import Interaction, SlashOption
from nextcord.ext import commands

import sqlite3
import datetime
from datetime import timedelta, timezone
import aiohttp
import io
import random
import asyncio
import os

# =========================
# 기본 설정
# =========================
intents = nextcord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.environ["TOKEN"]
LOG1_CHANNEL_ID = 1476575552268931217            # 입장로그 채널 ID
LOG2_CHANNEL_ID = 1476989472183812269        #재화로그 채널 ID
ENTRY_ROLE_ID = 1476991031072391228  # 입장 사용 가능한 역할 ID
LOG_CHANNEL_ID = 1476989549967179817  # 🔥 커플계좌로그

KST = timezone(timedelta(hours=9))
DB_FILE = "users.db"

user_sessions = {}

# =========================
# DB 초기화
# =========================

def db():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 👤 유저 테이블
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            balance INTEGER DEFAULT 0,
            last_checkin_time TEXT,
            spouse TEXT,
            couple_name TEXT
        )
    """)

    # 💑 커플 테이블 
    c.execute("""
        CREATE TABLE IF NOT EXISTS couples (
            couple_name TEXT PRIMARY KEY,
            husband_id TEXT,
            wife_id TEXT,
            balance INTEGER DEFAULT 0,
            married_at TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# =========================
# 쿨타임체크
# =========================
def is_on_cooldown(last_time_str, cooldown_minutes):
    if last_time_str is None:
        return False, 0
    try:
        last_time = datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        remaining = (last_time + timedelta(minutes=cooldown_minutes)) - now
        if remaining.total_seconds() > 0:
            return True, int(remaining.total_seconds())
        else:
            return False, 0
    except Exception:
        return False, 0

# =========================
# 유저 관련 함수
# =========================
def user_exists(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    exists = c.fetchone() is not None
    conn.close()
    return exists


def add_user(user_id, name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)",
        (user_id, name)
    )
    conn.commit()
    conn.close()

# =========================
# 이미지 다운로드 함수
# =========================
async def download_image(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return io.BytesIO(await resp.read())
    return None

# =========================
# 모달
# =========================
class EntryModal(nextcord.ui.Modal):
    def __init__(self, user):
        super().__init__(title="입장 정보 입력")
        self.user = user

        self.name = nextcord.ui.TextInput(label="이름", required=True)
        self.age = nextcord.ui.TextInput(label="나이(년생)", required=True)
        self.gender = nextcord.ui.TextInput(label="성별", required=True)
        self.entry_path = nextcord.ui.TextInput(
            label="입장 경로",
            placeholder="예: 친구(안내 시 친구분을 태그해주세요) / 디스보드 / 연합서버 이름",
            required=True
        )

        self.add_item(self.name)
        self.add_item(self.age)
        self.add_item(self.gender)
        self.add_item(self.entry_path)

    async def callback(self, interaction: Interaction):
        user_sessions[self.user.id] = {
            "step": 2,
            "start_time": datetime.datetime.now(KST),
            "data": {
                "info": f"{self.name.value} / {self.age.value} / {self.gender.value}",
                "entry_path": self.entry_path.value
            }
        }

        await interaction.response.send_message(
            "**경로 사진을 업로드해주세요.**",
            view=RoutePhotoView(self.user),
            ephemeral=True
        )

# =========================
# 버튼 View
# =========================
class RoutePhotoView(nextcord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=300)
        self.user = user

    @nextcord.ui.button(label="다음", style=nextcord.ButtonStyle.primary)
    async def next_step(self, button, interaction: Interaction):
        session = user_sessions.get(self.user.id)
        if not session or "route_img" not in session["data"]:
            await interaction.response.send_message(
                "먼저 경로 사진을 업로드해주세요.",
                ephemeral=True
            )
            return

        session["step"] = 3
        await interaction.response.send_message(
            "**추천 사진을 업로드해주세요.**",
            view=RecommendPhotoView(self.user),
            ephemeral=True
        )

class RecommendPhotoView(nextcord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=300)
        self.user = user

    @nextcord.ui.button(label="완료", style=nextcord.ButtonStyle.success)
    async def finish(self, button, interaction: Interaction):
        session = user_sessions.get(self.user.id)
        if not session or "recommend_img" not in session["data"]:
            await interaction.response.send_message(
                "추천 사진을 업로드해주세요.",
                ephemeral=True
            )
            return

        log_channel = bot.get_channel(LOG1_CHANNEL_ID)
        used_time = datetime.datetime.now(KST) - session["start_time"]

        base_embed = nextcord.Embed(
            title="신규 입장 로그",
            color=0x2ecc71,
            timestamp=datetime.datetime.now(KST)

        )
        base_embed.add_field(name="유저", value=interaction.user.mention, inline=False)
        base_embed.add_field(name="유저 ID", value=interaction.user.id, inline=False)
        base_embed.add_field(name="입력 정보", value=session["data"]["info"], inline=False)
        base_embed.add_field(name="입장 경로", value=session["data"]["entry_path"], inline=False)
        base_embed.add_field(name="봇 사용 시간", value=str(used_time), inline=False)

        # 이미지 파일로 재업로드
        route_file = await download_image(session["data"]["route_img"])
        recommend_file = await download_image(session["data"]["recommend_img"])

        files = []
        if route_file:
            files.append(nextcord.File(route_file, filename="route.png"))
        if recommend_file:
            files.append(nextcord.File(recommend_file, filename="recommend.png"))

        await log_channel.send(
            embed=base_embed,
            files=files
        )

        # 원본 사진 메시지 삭제
        try:
            ch = await bot.fetch_channel(session["data"]["route_channel_id"])
            msg = await ch.fetch_message(session["data"]["route_msg_id"])
            await msg.delete()

            ch = await bot.fetch_channel(session["data"]["recommend_channel_id"])
            msg = await ch.fetch_message(session["data"]["recommend_msg_id"])
            await msg.delete()
        except:
            pass

        user_sessions.pop(self.user.id, None)

        await interaction.response.send_message(
            "입장 신청이 완료되었습니다.",
            ephemeral=True
        )

# =========================
# 사진 감지
# =========================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    session = user_sessions.get(message.author.id)

    # 세션 + 첨부파일이 있을 때만 사진 처리
    if session and message.attachments:
        attachment = message.attachments[0]

        if attachment.content_type and attachment.content_type.startswith("image"):

            if attachment.content_type in ["image/heic", "image/heif"]:
                await message.channel.send(
                    "HEIC 형식은 지원되지 않습니다.\n"
                    "아이폰 사용자는 사진을 캡쳐해서 업로드해주세요.",
                    delete_after=5
                )

            elif session["step"] == 2:
                session["data"]["route_img"] = attachment.url
                session["data"]["route_msg_id"] = message.id
                session["data"]["route_channel_id"] = message.channel.id
                await message.channel.send(
                    "**경로 사진이 등록되었습니다. 다음 버튼을 눌러주세요**",
                    delete_after=3
                )

            elif session["step"] == 3:
                session["data"]["recommend_img"] = attachment.url
                session["data"]["recommend_msg_id"] = message.id
                session["data"]["recommend_channel_id"] = message.channel.id
                await message.channel.send(
                    "**추천 사진이 등록되었습니다. 완료 버튼을 눌러주세요**",
                    delete_after=3
                )

    await bot.process_commands(message)


# =========================
# 슬래시 명령어
# =========================
@bot.slash_command(name="입장", description="입장신청을 도와드립니다.")
async def enter(interaction: Interaction):
    role = interaction.guild.get_role(ENTRY_ROLE_ID)
    if role not in interaction.user.roles:
        await interaction.response.send_message(
            "이 명령어를 사용할 권한이 없습니다.",
            ephemeral=True
        )
        return

    await interaction.response.send_modal(EntryModal(interaction.user))

@bot.event
async def on_ready():
    await bot.sync_application_commands()
    print(f'Logged in as {bot.user}')

# =========================
# 가입 / 탈퇴
# =========================
@bot.slash_command(name="가입")
async def 체크인(interaction: Interaction):
    uid = str(interaction.user.id)
    if user_exists(uid):
        await interaction.response.send_message("이미 가입되어 있습니다.", ephemeral=True)
        return
    add_user(uid, interaction.user.name)
    await interaction.response.send_message("가입 완료 ", ephemeral=False)


@bot.slash_command(name="탈퇴")
async def 체크아웃(interaction: Interaction):
    uid = str(interaction.user.id)
    if not user_exists(uid):
        await interaction.response.send_message("가입되어 있지 않습니다.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    await interaction.response.send_message("탈퇴 완료", ephemeral=True)

# =========================
# 재화
# =========================
@bot.slash_command(name="잔액", description="유저의 현재 잔액을 보여드립니다.")
async def 잔액(interaction: Interaction):
    uid = str(interaction.user.id)
    if not user_exists(uid):
        await interaction.response.send_message("가입 해주세요.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (uid,))
    balance = c.fetchone()[0]
    conn.close()

    embed = nextcord.Embed(
        title=interaction.user.name,
        description="현재 잔액",
        color=0xF3F781
    )
    embed.add_field(name="잔액", value=f"{balance}원", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.slash_command(name="출석", description="하루에 한번만 출석이 가능합니다.")
async def 출석(interaction: Interaction):
    uid = str(interaction.user.id)
    if not user_exists(uid):
        await interaction.response.send_message("가입 해주세요.", ephemeral=True)
        return

    now = datetime.datetime.now(KST)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT balance, last_checkin_time FROM users WHERE user_id = ?", (uid,))
    balance, last_time = c.fetchone()

    if last_time:
        last = datetime.datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        if last.date() == now.date():
            await interaction.response.send_message("이미 오늘 출석했습니다.", ephemeral=True)
            conn.close()
            return

    reward = 100
    balance += reward

    c.execute(
        "UPDATE users SET balance = ?, last_checkin_time = ? WHERE user_id = ?",
        (balance, now.strftime("%Y-%m-%d %H:%M:%S"), uid)
    )
    conn.commit()
    conn.close()

    embed = nextcord.Embed(title="출석 완료", color=0x76FF7A)
    embed.add_field(name="보상", value=f"{reward}원", inline=True)
    embed.add_field(name="현재 잔액", value=f"{balance}원", inline=True)

    await interaction.response.send_message(embed=embed)


@bot.slash_command(name="잔액랭킹", description="유저들의 잔액 랭킹을 보여줍니다.")
async def 잔액랭킹(interaction: Interaction):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, balance FROM users ORDER BY balance DESC LIMIT 5")
    rows = c.fetchall()
    conn.close()

    embed = nextcord.Embed(title="잔액 랭킹 TOP 5", color=0xFFD700)
    for i, (name, bal) in enumerate(rows, start=1):
        embed.add_field(name=f"{i}위 - {name}", value=f"{bal}원", inline=False)

    await interaction.response.send_message(embed=embed)


# --- 잔액 변경 (관리자만) ---
@bot.slash_command(name="잔액변경", description="유저의 잔액을 변경할 수 있습니다.", default_member_permissions=nextcord.Permissions(administrator=True))
async def 잔액변경(
    interaction: Interaction,
    유저: nextcord.Member = nextcord.SlashOption(description="유저를 선택하세요."),
    사유: str = nextcord.SlashOption(description="변경 사유를 입력하세요."),
    변경할금액: int = nextcord.SlashOption(description="변경할 금액을 입력하세요.")
):
    if not (interaction.user.guild_permissions.administrator or interaction.guild.owner_id == interaction.user.id):
        await interaction.response.send_message("관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return

    user_id = str(유저.id)
    if not user_exists(user_id):
        await interaction.response.send_message("가입 되어있지 않거나 존재하지 않는 유저입니다.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    current_balance = c.fetchone()[0] or 0
    new_balance = current_balance + 변경할금액
    c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()
    conn.close()

    embed = nextcord.Embed(
        title=f"{interaction.user.name}님의 요청",
        description=f"{유저.mention}님의 잔액 변경 완료!",
        color=nextcord.Color(0xF3F781)
    )
    embed.add_field(name="변경한 금액", value=f"{변경할금액}원", inline=False)
    embed.add_field(name="현재 잔액", value=f"{new_balance}원", inline=False)
    embed.add_field(name="사유", value=사유, inline=False)
    await interaction.response.send_message(embed=embed)

        # 임베드
    log_embed = nextcord.Embed(
        title="잔액 변경 로그",
        color=nextcord.Color.orange(),
        timestamp=interaction.created_at
    )
    log_embed.add_field(name="변경자", value=interaction.user.mention, inline=True)
    log_embed.add_field(name="대상", value=유저.mention, inline=True)
    log_embed.add_field(name="금액", value=f"{변경할금액}원", inline=True)
    log_embed.add_field(name="변경 후 잔액", value=f"{new_balance}원", inline=True)
    log_embed.add_field(name="사유", value=사유, inline=False)
    log_embed.set_footer(text=f"명령어 실행 시간: {interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

    # 로그 채널에 전송
    log_channel = interaction.guild.get_channel(LOG2_CHANNEL_ID)
    if log_channel:
        await log_channel.send(embed=log_embed)



# =========================
# 닉네임 변경
# =========================
@bot.command(name="닉네임변경")
async def 닉네임변경(ctx, *, 새_닉네임: str):

    if not ctx.guild.me.guild_permissions.manage_nicknames:
        await ctx.send("봇에게 '닉네임 변경' 권한이 없습니다.")
        return

    try:
        user = ctx.author
        roles = user.roles

        # 🔹 팀 역할
        TEAM_ROLES = {
            1476575548150120569: "뉴관",
            1476575548162576535: "보안",
            1476575548150120566: "홍보",
            1476575548150120568: "기획",
            1476575548150120567: "안내",
            1476575548150120565: "내전"
        }

        # 🔹 직급 역할
        RANK_ROLES = {
            1476575548162576539: "팀장",
            1476575548162576537: "부팀장",
            1476575548162576536: "팀원",
            1476575548150120564: "인턴"
        }

        # 🔹 후원 역할
        DONATION_ROLES = {
            1476575548116439250: "꒰ 𝑽𝑰𝑷 ꒱",
            1476575548116439251: "꒰ 𝑽𝑽𝑰𝑷 ꒱",
            1476575548116439252: "꒰ 𝑺𝑽𝑰𝑷 ꒱",
            1476575548116439253: "꒰ 𝑬𝑽𝑷 ꒱"
        }

        # 🔹 일반 서버원
        NORMAL_ROLES = {
            1476575548083015685: "⟢ 〖𝚨〗",
            1476575548083015686: "⟢ 〖𝛀〗"
        }

        team_name = None
        rank_name = None
        donation_prefix = None
        normal_prefix = None

        # 팀 찾기
        for role_id, name in TEAM_ROLES.items():
            if nextcord.utils.get(roles, id=role_id):
                team_name = name
                break

        # 직급 찾기
        for role_id, name in RANK_ROLES.items():
            if nextcord.utils.get(roles, id=role_id):
                rank_name = name
                break

        # 🔥 팀만 있는 경우 차단
        if team_name and not rank_name:
            await ctx.send("팀 역할만으로는 닉네임을 변경할 수 없습니다. 직급 역할이 필요합니다.")
            return

        # 후원 찾기
        for role_id, prefix in DONATION_ROLES.items():
            if nextcord.utils.get(roles, id=role_id):
                donation_prefix = prefix
                break

        # 일반 역할 찾기
        for role_id, prefix in NORMAL_ROLES.items():
            if nextcord.utils.get(roles, id=role_id):
                normal_prefix = prefix
                break

        # 🔥 우선순위 적용

        # 1️⃣ 팀 + 직급
        if team_name and rank_name:
            final_nickname = f"[ {team_name} {rank_name} ] {새_닉네임}"

        # 2️⃣ 후원
        elif donation_prefix:
            final_nickname = f"{donation_prefix} {새_닉네임}"

        # 3️⃣ 일반 서버원
        elif normal_prefix:
            final_nickname = f"{normal_prefix} {새_닉네임}"

        else:
            final_nickname = 새_닉네임

        await user.edit(nick=final_nickname)

        await ctx.send(f"{user.mention}님의 닉네임이 `{final_nickname}`(으)로 변경되었습니다.")

        # 규칙 임베드
        embed = nextcord.Embed(
            title="별명 변경 규칙",
            description=(
                "-------------------------\n"
                "!닉네임변경 (원하는 닉네임)\n"
                "-------------------------\n"
                "**사용 불가 목록**\n"
                "1. 띄어쓰기 포함 8글자 이상\n"
                "2. 이모지 / 특수문자 포함\n"
                "3. 정치 관련\n"
                "4. 불쾌감 유발\n"
                "5. 외국어 닉네임\n\n"
                "위반 시 닉네임이 별명변경대상으로 변경됩니다."
            ),
            color=nextcord.Color.red()
        )

        await ctx.send(embed=embed)

    except nextcord.Forbidden:
        await ctx.send("닉네임 변경 권한이 부족합니다.")

    except Exception as e:
        await ctx.send(f"닉네임 변경 중 오류 발생: {e}")

# =========================

MARRIED_ROLE_ID = 1477458582931902676  # 💑 부부 역할 ID 넣기

class ProposalView(nextcord.ui.View):
    def __init__(self, proposer, partner, couple_name):
        super().__init__(timeout=60)
        self.proposer = proposer
        self.partner = partner
        self.couple_name = couple_name

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.partner.id:
            await interaction.response.send_message(
                "당사자만 선택할 수 있습니다.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    # 💖 수락
    @nextcord.ui.button(label="수락 💖", style=nextcord.ButtonStyle.success)
    async def accept(self, button, interaction: Interaction):

        guild = interaction.guild

        await interaction.response.defer()  # 상호작용 안전 처리

        with db() as conn:
            c = conn.cursor()

            # 🔥 두 사람 모두 결혼 여부 확인
            c.execute("SELECT spouse FROM users WHERE user_id=?", (str(self.proposer.id),))
            proposer_row = c.fetchone()

            c.execute("SELECT spouse FROM users WHERE user_id=?", (str(self.partner.id),))
            partner_row = c.fetchone()

            if not proposer_row or not partner_row:
                await interaction.followup.send("가입되지 않은 유저가 있습니다.", ephemeral=True)
                return

            if proposer_row[0] or partner_row[0]:
                await interaction.followup.send("이미 결혼한 사람이 있습니다.", ephemeral=True)
                return

            # 🔥 커플 이름 중복 방지
            c.execute("SELECT 1 FROM couples WHERE couple_name=?", (self.couple_name,))
            if c.fetchone():
                await interaction.followup.send("이미 존재하는 커플명입니다.", ephemeral=True)
                return

            # 커플 생성
            c.execute("""
                INSERT INTO couples (couple_name, husband_id, wife_id, balance, married_at)
                VALUES (?, ?, ?, 0, ?)
            """, (
                self.couple_name,
                str(self.proposer.id),
                str(self.partner.id),
                datetime.datetime.now().isoformat()
            ))

            # 유저 업데이트
            c.execute("UPDATE users SET spouse=?, couple_name=? WHERE user_id=?",
                    (str(self.partner.id), self.couple_name, str(self.proposer.id)))
            c.execute("UPDATE users SET spouse=?, couple_name=? WHERE user_id=?",
                    (str(self.proposer.id), self.couple_name, str(self.partner.id)))

            conn.commit()

        # 💑 역할 지급
        role = guild.get_role(MARRIED_ROLE_ID)
        if role:
            await self.proposer.add_roles(role)
            await self.partner.add_roles(role)

        embed = nextcord.Embed(
            title="💍 결혼을 축하합니다!",
            description=f"{self.proposer.mention} ❤️ {self.partner.mention}\n"
                        f"👑 커플명 : **{self.couple_name}**\n"
                        f"💰 공동계좌가 생성되었습니다!",
            color=0xff69b4
        )

        await interaction.message.edit(embed=embed, view=None)

    # 💔 거절
    @nextcord.ui.button(label="거절 💔", style=nextcord.ButtonStyle.danger)
    async def decline(self, button, interaction: Interaction):

        embed = nextcord.Embed(
            title="💔 프로포즈 거절",
            description=f"{self.partner.mention}님이 프로포즈를 거절했습니다.",
            color=0x808080
        )

        await interaction.response.edit_message(embed=embed, view=None)


@bot.slash_command(name="프로포즈", description="상대에게 프로포즈합니다.")
async def propose(interaction: Interaction, 상대: nextcord.Member, 커플이름: str):

    if 상대.bot or 상대.id == interaction.user.id:
        await interaction.response.send_message("올바른 대상을 선택하세요.", ephemeral=True)
        return

    if not user_exists(str(interaction.user.id)):
        await interaction.response.send_message("가입 후 이용해주세요.", ephemeral=True)
        return

    if not user_exists(str(상대.id)):
        await interaction.response.send_message("상대가 가입되어 있지 않습니다.", ephemeral=True)
        return

    with db() as conn:
        c = conn.cursor()

        # 이미 결혼 여부
        c.execute("SELECT spouse FROM users WHERE user_id=?", (str(interaction.user.id),))
        if c.fetchone()[0]:
            await interaction.response.send_message("이미 결혼한 상태입니다.", ephemeral=True)
            return

        c.execute("SELECT spouse FROM users WHERE user_id=?", (str(상대.id),))
        if c.fetchone()[0]:
            await interaction.response.send_message("상대가 이미 결혼한 상태입니다.", ephemeral=True)
            return

        # 커플 이름 중복
        c.execute("SELECT 1 FROM couples WHERE couple_name=?", (커플이름,))
        if c.fetchone():
            await interaction.response.send_message("이미 존재하는 커플 이름입니다.", ephemeral=True)
            return

    embed = nextcord.Embed(
        title="💍 프로포즈!",
        description=f"{interaction.user.mention} ❤️ {상대.mention}\n커플명: {커플이름}"
    )

    await interaction.response.send_message(
        embed=embed,
        view=ProposalView(interaction.user, 상대, 커플이름)
    )

@bot.slash_command(name="축의금", description="커플에게 축의금을 보냅니다.")
async def gift(interaction: Interaction, 커플이름: str, 금액: int):

    uid = str(interaction.user.id)

    with db() as conn:
        c = conn.cursor()

        # 개인 돈 확인
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = c.fetchone()

        if not row or row[0] < 금액:
            await interaction.response.send_message("재화가 부족합니다.", ephemeral=True)
            return

        # 커플 존재 확인
        c.execute("SELECT balance FROM couples WHERE couple_name=?", (커플이름,))
        couple = c.fetchone()
        if not couple:
            await interaction.response.send_message("존재하지 않는 커플입니다.", ephemeral=True)
            return

        # 돈 이동
        c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (금액, uid))
        c.execute("UPDATE couples SET balance = balance + ? WHERE couple_name=?", (금액, 커플이름))

        conn.commit()

    await interaction.response.send_message(
        f"💝 {커플이름} 커플 계좌에 {금액}원이 추가되었습니다!"
    )

@bot.slash_command(name="이혼", description="이혼합니다.")
async def divorce(interaction: Interaction, 커플이름: str):

    guild = interaction.guild

    with db() as conn:
        c = conn.cursor()

        c.execute("SELECT husband_id, wife_id, balance FROM couples WHERE couple_name=?", (커플이름,))
        row = c.fetchone()

        if not row:
            await interaction.response.send_message("존재하지 않는 커플입니다.", ephemeral=True)
            return

        husband_id, wife_id, balance = row
        split = balance // 2

        # 개인 계좌 지급 + 관계 초기화
        c.execute("UPDATE users SET balance = balance + ?, spouse=NULL, couple_name=NULL WHERE user_id=?",
                  (split, husband_id))
        c.execute("UPDATE users SET balance = balance + ?, spouse=NULL, couple_name=NULL WHERE user_id=?",
                  (split, wife_id))

        c.execute("DELETE FROM couples WHERE couple_name=?", (커플이름,))
        conn.commit()

    # 💑 역할 제거
    role = guild.get_role(MARRIED_ROLE_ID)

    try:
        husband = await guild.fetch_member(int(husband_id))
        wife = await guild.fetch_member(int(wife_id))

        if role:
            if husband:
                await husband.remove_roles(role)
            if wife:
                await wife.remove_roles(role)

    except nextcord.Forbidden:
        await interaction.response.send_message(
            "⚠ 봇 역할이 결혼 역할보다 낮습니다.",
            ephemeral=True
        )
        return

    except Exception as e:
        await interaction.response.send_message(
            f"역할 제거 오류: {e}",
            ephemeral=True
        )
        return


    # ✅ 성공 메시지
    await interaction.response.send_message(
        f"💔 더 좋은 사람을 만나길 바라요...\n"
        f"위자료 : {split}원씩 지급되었습니다."
    )

@bot.slash_command(name="커플정보", description="내 커플 정보를 확인합니다.")
async def couple_info(interaction: Interaction):

    uid = str(interaction.user.id)

    with db() as conn:
        c = conn.cursor()

        c.execute("SELECT couple_name FROM users WHERE user_id=?", (uid,))
        row = c.fetchone()

        if not row or not row[0]:
            await interaction.response.send_message("현재 결혼 상태가 아닙니다.", ephemeral=True)
            return

        couple_name = row[0]

        c.execute("""
            SELECT husband_id, wife_id, balance, married_at
            FROM couples WHERE couple_name=?
        """, (couple_name,))
        couple = c.fetchone()

    husband_id, wife_id, balance, married_at = couple

    embed = nextcord.Embed(
        title=f"💑 {couple_name} 커플 정보",
        color=0xff69b4
    )

    embed.add_field(
        name="배우자",
        value=f"<@{husband_id}> ❤️ <@{wife_id}>",
        inline=False
    )

    embed.add_field(
        name="공동 계좌",
        value=f"{balance}원",
        inline=False
    )

    embed.add_field(
        name="결혼 날짜",
        value=married_at[:10],
        inline=False
    )

    await interaction.response.send_message(embed=embed)

@bot.slash_command(name="커플통장", description="공동 계좌 잔액 확인")
async def couple_account(interaction: Interaction):

    uid = str(interaction.user.id)

    with db() as conn:
        c = conn.cursor()

        c.execute("SELECT couple_name FROM users WHERE user_id=?", (uid,))
        row = c.fetchone()

        if not row or not row[0]:
            await interaction.response.send_message("결혼 상태가 아닙니다.", ephemeral=True)
            return

        couple_name = row[0]

        c.execute("SELECT balance FROM couples WHERE couple_name=?", (couple_name,))
        balance = c.fetchone()[0]

    await interaction.response.send_message(
        f"💰 {couple_name} 공동 계좌 잔액 : {balance}원"
    )

@bot.slash_command(name="커플목록", description="서버의 커플 목록을 확인합니다.")
async def couples_list(interaction: Interaction):

    with db() as conn:
        c = conn.cursor()
        c.execute("SELECT couple_name, husband_id, wife_id, balance FROM couples")
        rows = c.fetchall()

    if not rows:
        await interaction.response.send_message("현재 등록된 커플이 없습니다.")
        return

    embed = nextcord.Embed(
        title="💑 커플 목록",
        color=0xffc0cb
    )

    for name, h, w, bal in rows:
        embed.add_field(
            name=name,
            value=f"<@{h}> ❤️ <@{w}>\n💰 {bal}원",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

from nextcord.ext import commands

from nextcord.ext import commands

@bot.slash_command(name="커플계좌변경", description="관리자 전용 커플 계좌 증감")
async def edit_couple_account(interaction: Interaction, 커플이름: str, 금액: int):

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("관리자만 사용 가능합니다.", ephemeral=True)
        return

    with db() as conn:
        c = conn.cursor()

        # 현재 잔액 조회
        c.execute("SELECT balance FROM couples WHERE couple_name=?", (커플이름,))
        row = c.fetchone()

        if not row:
            await interaction.response.send_message("존재하지 않는 커플입니다.", ephemeral=True)
            return

        old_balance = row[0]

        # 🔥 증감 방식
        new_balance = old_balance + 금액

        # 마이너스 방지 (선택사항)
        if new_balance < 0:
            await interaction.response.send_message("잔액은 0원 이하가 될 수 없습니다.", ephemeral=True)
            return

        # 업데이트
        c.execute(
            "UPDATE couples SET balance=? WHERE couple_name=?",
            (new_balance, 커플이름)
        )
        conn.commit()

    sign = "증가" if 금액 > 0 else "감소"

    await interaction.response.send_message(
        f"🔧 {커플이름} 계좌가 {abs(금액)}원 {sign}되었습니다.\n"
        f"💰 현재 잔액: {new_balance}원"
    )
    # =========================
    # 로그 기록
    # =========================
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)

    if log_channel:
        embed = nextcord.Embed(
            title="커플계좌 변경 로그",
            color=0xff0000
        )
        embed.add_field(name="관리자", value=interaction.user.mention, inline=False)
        embed.add_field(name="커플명", value=커플이름, inline=False)
        embed.add_field(name="변경 전 금액", value=f"{old_balance}원", inline=False)
        embed.add_field(name="변경 후 금액", value=f"{금액}원", inline=False)
        embed.add_field(name="변경 시간", value=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), inline=False)

        await log_channel.send(embed=embed)

# =========================

@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')
    print("등록된 명령어 목록:", [cmd.name for cmd in bot.commands])

# =========================
# 실행
# =========================
bot.run(TOKEN)
