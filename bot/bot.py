"""Discord front-end. Prefix commands mirror the MWP DEOBF panel: .deobf .<engine> .detect .l .get .envlog .engines .help"""
import asyncio
import io
import os
import re

import discord
from discord.ext import commands

from deobf import ENGINES, deobfuscate, detect, logger, trace_behavior, with_header
from deobf.envlog import LOADER, pick_final
from deobf.fetch import FetchError, extract_loader_url, fetch


def load_env(path=".env"):
    """KEY=VALUE lines from .env (does not override variables already set)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip("\"'"))
    except OSError:
        pass


load_env()
PREFIX = os.environ.get("BOT_PREFIX", ".")
MAX_FILES = 10
MAX_BYTES = 2 * 1024 * 1024
FETCH = os.environ.get("ALLOW_SCRIPT_FETCH") == "1"  # let scripts pull their next stage via HttpGet (public urls only)
JOBS = asyncio.Semaphore(int(os.environ.get("MAX_JOBS", "4")))
EXTS = (".lua", ".luau", ".txt")
FENCE_RE = re.compile(r"```(?:lua|luau)?\n?(.*?)```", re.S)
ENGINE_NAMES = [e.name for e in ENGINES]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


def _b(text):
    return io.BytesIO(text.encode("latin-1", "replace"))


def _file(text, name):
    return discord.File(_b(text), filename=name)


async def _read_attachments(msg):
    out = []
    for a in msg.attachments[:MAX_FILES]:
        if not a.filename.lower().endswith(EXTS):
            continue
        if a.size > MAX_BYTES:
            raise commands.UserInputError("%s is over 2 MB" % a.filename)
        out.append((a.filename, (await a.read()).decode("latin-1")))
    return out


async def gather_inputs(ctx, arg):
    """Attachments on the message, on the replied-to message, a code block, a url, or raw pasted code."""
    items = await _read_attachments(ctx.message)
    ref = ctx.message.reference
    if not items and ref:
        target = ref.resolved or await ctx.channel.fetch_message(ref.message_id)
        items = await _read_attachments(target)
        if not items and getattr(target, "content", ""):
            arg = arg or target.content
    if not items and arg:
        arg = arg.strip()
        fence = FENCE_RE.search(arg)
        if fence:
            items = [("paste.lua", fence.group(1))]
        elif extract_loader_url(arg.strip("<>")):
            try:
                items = [("fetched.lua", await asyncio.to_thread(fetch, extract_loader_url(arg.strip("<>"))))]
            except FetchError as e:
                raise commands.UserInputError("fetch failed: %s" % e)
        else:
            items = [("paste.lua", arg)]
    if not items:
        raise commands.UserInputError("attach up to 10 .lua/.luau/.txt files, paste code, give a url, or reply to a sample")
    return items


async def _job(fn, *a, **kw):
    async with JOBS:
        return await asyncio.to_thread(fn, *a, **kw)


def _summary(name, r):
    emb = discord.Embed(title="deobfuscated: %s" % name, colour=0x57F287 if r.changed else 0x99AAB5)
    top = ", ".join("%s %d%%" % c for c in r.candidates[:3]) or "no signature match"
    emb.add_field(name="engine", value="%s (%d%%)" % (r.engine, r.confidence))
    emb.add_field(name="layers", value=str(r.layers))
    emb.add_field(name="obfuscation score", value="%d/100" % r.obf_score)
    emb.add_field(name="candidates", value=top, inline=False)
    if r.notes:
        emb.add_field(name="notes", value="\n".join(sorted(set(r.notes)))[:1000], inline=False)
    if r.urls:
        emb.add_field(name="urls seen", value="\n".join(r.urls[:6])[:1000], inline=False)
    if r.error:
        emb.add_field(name="runtime", value=r.error[:300], inline=False)
    if r.findings:
        kinds = sorted({"%s (%s)" % (f.desc, f.severity) for f in r.findings})
        emb.add_field(name="logger scan" + (" - LOGGER DETECTED" if r.is_logger else ""), value="\n".join(kinds)[:1000], inline=False)
        if r.is_logger:
            emb.colour = 0xED4245
    return emb


class RemoveLogger(discord.ui.Button):
    def __init__(self, name, body, from_trace):
        super().__init__(label="Remove logger", style=discord.ButtonStyle.danger, emoji="🚨")
        self.name, self.body, self.from_trace = name, body, from_trace

    async def callback(self, inter):
        await inter.response.defer(thinking=True)
        if self.from_trace:
            clean, notes = logger.remove_from_trace(self.body)
        else:
            clean, notes = await _job(logger.remove, self.body)
        await inter.followup.send("; ".join(notes) or "nothing to strip", file=_file(with_header(clean), "clean_" + self.name))


class EnginePicker(discord.ui.Select):
    def __init__(self, name, raw):
        super().__init__(placeholder="re-run as another engine", options=[discord.SelectOption(label=e.label, value=e.name) for e in ENGINES])
        self.name, self.raw = name, raw

    async def callback(self, inter):
        await inter.response.defer(thinking=True)
        r = await _job(deobfuscate, self.raw, engine=self.values[0], fetch=FETCH)
        await inter.followup.send(embed=_summary(self.name, r), file=_file(r.best, "deobf_" + self.name))


class ResultView(discord.ui.View):
    def __init__(self, name, raw, r):
        super().__init__(timeout=900)
        if r.is_logger:
            self.add_item(RemoveLogger(name, r.body, r.shows_recon))
        self.add_item(EnginePicker(name, raw))


async def run_deobf(ctx, arg, engine=None):
    items = await gather_inputs(ctx, arg)
    async with ctx.typing():
        results = await asyncio.gather(*[_job(deobfuscate, text, engine=engine, fetch=FETCH) for _, text in items])
    for (name, raw), r in zip(items, results):
        await ctx.reply(embed=_summary(name, r), file=_file(r.best, "deobf_" + os.path.basename(name)), view=ResultView(name, raw, r), mention_author=False)


@bot.command(name="deobf")
async def deobf_cmd(ctx, *, arg=""):
    await run_deobf(ctx, arg)


def _make_engine_cmd(name):
    async def cmd(ctx, *, arg=""):
        await run_deobf(ctx, arg, engine=name)

    cmd.__name__ = name
    bot.command(name=name)(cmd)


for _n in ENGINE_NAMES:
    _make_engine_cmd(_n)


@bot.command(name="detect")
async def detect_cmd(ctx, *, arg=""):
    items = await gather_inputs(ctx, arg)
    lines = []
    for name, text in items:
        ranked, score = detect(text)
        body = ", ".join("%s %d%%" % c for c in ranked) or "no known signature"
        lines.append("**%s** - %s | obfuscation score %d/100" % (name, body, score))
    await ctx.reply("\n".join(lines)[:1900], mention_author=False)


@bot.command(name="l")
async def luau_cmd(ctx, *, arg=""):
    items = await gather_inputs(ctx, arg)
    async with ctx.typing():
        outs = await asyncio.gather(*[_job(trace_behavior, text) for _, text in items])
    for (name, _), (text, _run) in zip(items, outs):
        await ctx.reply("behaviour trace: **%s**" % name, file=_file(text, "trace_" + os.path.basename(name)), mention_author=False)


class FetchView(discord.ui.View):
    def __init__(self, name, raw):
        super().__init__(timeout=900)
        self.name, self.raw = name, raw

    @discord.ui.button(label="Deobfuscate", style=discord.ButtonStyle.success)
    async def deob(self, inter, _):
        await inter.response.defer(thinking=True)
        r = await _job(deobfuscate, self.raw, fetch=FETCH)
        await inter.followup.send(embed=_summary(self.name, r), file=_file(r.best, "deobf_" + self.name), view=ResultView(self.name, self.raw, r))

    @discord.ui.button(label="Env Log", emoji="🧾", style=discord.ButtonStyle.secondary)
    async def envlog(self, inter, _):
        await inter.response.defer(thinking=True)
        text, _r = await _job(trace_behavior, self.raw)
        await inter.followup.send(file=_file(text, "envlog_" + self.name))


@bot.command(name="get")
async def get_cmd(ctx, url: str):
    try:
        raw = await asyncio.to_thread(fetch, url.strip("<>"))
    except FetchError as e:
        raise commands.UserInputError("fetch failed: %s" % e)
    name = (url.rstrip("/").rsplit("/", 1)[-1] or "script")[:40]
    name = name if name.lower().endswith(EXTS) else name + ".lua"
    await ctx.reply("fetched %d bytes" % len(raw), file=_file(raw, name), view=FetchView(name, raw), mention_author=False)


@bot.command(name="envlog")
async def envlog_cmd(ctx, *, arg=""):
    files = [f for f in await _read_attachments(ctx.message) if re.search(r"loadstring", f[0], re.I)]
    if not files:
        await ctx.reply("run this loader in your executor before the target script, then upload the `loadstring*.txt` files it writes with `.envlog`", file=_file(LOADER, "envlog_loader.lua"), mention_author=False)
        return
    name, text = pick_final(files)
    async with ctx.typing():
        r = await _job(deobfuscate, text, fetch=FETCH)
    await ctx.reply("analysed deepest capture: **%s** (%d file(s) uploaded)" % (name, len(files)), embed=_summary(name, r), file=_file(r.best, "deobf_" + name), view=ResultView(name, text, r), mention_author=False)


@bot.command(name="engines")
async def engines_cmd(ctx):
    lines = ["`.%s` - %s%s" % (e.name, e.label, " (vm)" if e.vm else "") for e in ENGINES]
    active = int(os.environ.get("MAX_JOBS", "4")) - JOBS._value
    await ctx.reply("\n".join(lines) + "\n\nqueue: %d running, limit %s" % (active, os.environ.get("MAX_JOBS", "4")), mention_author=False)


HELP = """**MWP DEOBF**
**Deobfuscation**
`.deobf` - detect + pick engine (unified panel)
{engines}
`.detect` - identify the obfuscator (with confidence)
`.l` - reconstruct observable Luau behaviour
**Tools**
`.get <url>` - fetch a script, then Deobfuscate or Env Log it
`.envlog` - executor-side capture loader (or analyse uploaded `loadstring*.txt`)
`.engines` - supported obfuscators + queue status
`.help` - this message
**Safety**
every result is scanned for item-stealer / data-exfil chunks; if found you get a Remove logger button.
attach up to 10 .lua/.luau/.txt files, paste code, give a url, or reply to a sample. files run in parallel."""


@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.reply(HELP.format(engines=" · ".join("`.%s`" % n for n in ENGINE_NAMES)), mention_author=False)


@bot.event
async def on_command_error(ctx, err):
    err = getattr(err, "original", err)
    if isinstance(err, commands.CommandNotFound):
        return
    if isinstance(err, (commands.UserInputError, commands.MissingRequiredArgument)):
        await ctx.reply(str(err), mention_author=False)
        return
    await ctx.reply("failed: %s" % type(err).__name__, mention_author=False)
    raise err


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("set DISCORD_TOKEN (bot token from the discord developer portal)")
    bot.run(token)


if __name__ == "__main__":
    main()
