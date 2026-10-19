"""A3K Deobfuscator Discord front-end: .deobf .<engine> .detect .l .get .envlog .engines .help."""
import asyncio
import io
import gzip
import json
import logging
import os
import re

import discord
from discord.ext import commands

from deobf import ENGINES, Result, analyze, deobfuscate, logger, trace_behavior, with_header
from deobf.sandbox import Run
from deobf.envlog import LOADER, pick_final
from deobf.fetch import FetchError, extract_loader_url, fetch
from .jobs import JobError, ProcessJobs


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


def _env_int(name, default, low, high):
    try:
        value = int(os.environ.get(name, str(default)))
        if not low <= value <= high:
            raise ValueError
        return value
    except ValueError:
        logging.warning("Invalid %s setting; using %d", name, default)
        return default


PREFIX = os.environ.get("BOT_PREFIX", ".")
MAX_FILES = 10
MAX_BYTES = 2 * 1024 * 1024
FETCH = os.environ.get("ALLOW_SCRIPT_FETCH") == "1"  # let scripts pull their next stage via HttpGet (public urls only)
JOBS = ProcessJobs(_env_int("MAX_JOBS", 1, 1, 4), _env_int("MAX_QUEUE", 8, 0, 100), _env_int("JOB_TIMEOUT", 75, 5, 300))
EXTS = (".lua", ".luau", ".txt")
FENCE_RE = re.compile(r"```(?:lua|luau)?\n?(.*?)```", re.S)
ENGINE_NAMES = [e.name for e in ENGINES]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None, description="A3K Deobfuscator", allowed_mentions=discord.AllowedMentions.none())


def _b(text):
    return io.BytesIO(text.encode("latin-1", "replace"))


def _file(text, name):
    data = text.encode("latin-1", "replace")
    name = os.path.basename(name.replace("\\", "/"))[:150] or "result.lua"
    if len(data) > 7 * 1024 * 1024:
        data, name = gzip.compress(data), name + ".gz"
    return discord.File(io.BytesIO(data), filename=name)


async def _read_attachments(msg):
    out = []
    for a in msg.attachments[:MAX_FILES]:
        if not a.filename.lower().endswith(EXTS):
            continue
        if a.size > MAX_BYTES:
            raise commands.UserInputError("%s is over 2 MB" % a.filename)
        data = await a.read()
        if len(data) > MAX_BYTES:
            raise commands.UserInputError("%s is over 2 MB" % a.filename)
        out.append((a.filename, data.decode("latin-1")))
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
    if any(len(src) > MAX_BYTES for _, src in items):
        raise commands.UserInputError("input is over 2 MB")
    return items


async def _job(fn, *a, **kw):
    action = {deobfuscate: "deobfuscate", trace_behavior: "trace", analyze: "analyze", logger.remove: "remove-logger"}[fn]
    if fn is deobfuscate:
        kw.setdefault("timeout", min(60, max(1, JOBS.timeout - 5)))
    result = await JOBS.run(action, a[0], **kw)
    if fn is deobfuscate:
        result["findings"] = [logger.Finding(**item) for item in result["findings"]]
        return Result(**result)
    if fn is trace_behavior:
        return result[0], Run(**result[1])
    return result


async def _batch(items, fn, **options):
    async def one(name, raw):
        try:
            return name, raw, await _job(fn, raw, **options)
        except Exception as error:
            return name, raw, error

    tasks = [asyncio.create_task(one(name, raw)) for name, raw in items]
    try:
        for done in asyncio.as_completed(tasks):
            yield await done
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _file_error(ctx, name, error):
    detail = str(error) if isinstance(error, (JobError, ValueError)) else type(error).__name__
    await ctx.reply("**%s**: %s" % (discord.utils.escape_markdown(name[:120]), detail[:350]), mention_author=False)


def _summary(name, r):
    emb = discord.Embed(title=("A3K Deobfuscator: %s" % name)[:256], colour=0x57F287 if r.changed else 0x99AAB5)
    emb.set_footer(text="Signature scores are heuristic. Recovery is best effort; VM output may be a behavior trace.")
    top = ", ".join("%s %d/100" % tuple(c) for c in r.candidates[:3]) or "no signature match"
    emb.add_field(name="engine / match score", value="%s (%d/100)" % (r.engine, r.confidence))
    emb.add_field(name="layers", value=str(r.layers))
    emb.add_field(name="output", value=r.output_kind)
    emb.add_field(name="time / status", value="%.2fs / %s" % (r.elapsed_seconds, r.stop_reason))
    if r.unresolved_urls:
        emb.add_field(name="remote payload unavailable", value="%d URL(s) were not retrieved. This is a partial result; analyze the downloaded Lua file or enable nested fetching explicitly." % len(r.unresolved_urls), inline=False)
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
    def __init__(self, name, raw, engines, group=1):
        super().__init__(placeholder="re-run as another engine (group %d)" % group, options=[discord.SelectOption(label=e.label, value=e.name) for e in engines])
        self.name, self.raw = name, raw

    async def callback(self, inter):
        await inter.response.defer(thinking=True)
        r = await _job(deobfuscate, self.raw, engine=self.values[0], fetch=FETCH)
        await inter.followup.send(embed=_summary(self.name, r), file=_file(r.best, "deobf_" + self.name))


class JobView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        text = str(error) if isinstance(error, JobError) else "The action failed: " + type(error).__name__
        if interaction.response.is_done():
            await interaction.followup.send(text[:500], ephemeral=True)
        else:
            await interaction.response.send_message(text[:500], ephemeral=True)


class ResultView(JobView):
    def __init__(self, name, raw, r):
        super().__init__(timeout=900)
        if r.is_logger:
            self.add_item(RemoveLogger(name, r.body, r.shows_recon))
        # Discord allows 25 options per select and five rows per view.
        for start in range(0, len(ENGINES), 25):
            self.add_item(EnginePicker(name, raw, ENGINES[start:start + 25], start // 25 + 1))


async def run_deobf(ctx, arg, engine=None, dynamic=True):
    items = await gather_inputs(ctx, arg)
    async with ctx.typing():
        async for name, raw, r in _batch(items, deobfuscate, engine=engine, fetch=FETCH, dynamic=dynamic):
            if isinstance(r, Exception):
                await _file_error(ctx, name, r)
                continue
            await ctx.reply(embed=_summary(name, r), file=_file(r.best, "deobf_" + os.path.basename(name)), view=ResultView(name, raw, r), mention_author=False)


@bot.command(name="deobf")
async def deobf_cmd(ctx, *, arg=""):
    await run_deobf(ctx, arg)


@bot.command(name="static")
async def static_cmd(ctx, *, arg=""):
    await run_deobf(ctx, arg, dynamic=False)


@bot.command(name="report")
async def report_cmd(ctx, *, arg=""):
    items = await gather_inputs(ctx, arg)
    async with ctx.typing():
        async for name, raw, r in _batch(items, deobfuscate, fetch=FETCH):
            if isinstance(r, Exception):
                await _file_error(ctx, name, r)
                continue
            text = json.dumps(r.to_report(), indent=2, ensure_ascii=True)
            await ctx.reply(embed=_summary(name, r), file=_file(text, "report_" + name + ".json"), mention_author=False)


@bot.command(name="status")
async def status_cmd(ctx):
    await ctx.reply("A3K Deobfuscator | %d profiles | %d/%d running | %d queued (capacity %d) | %ds job limit" %
                    (len(ENGINES), JOBS.active, JOBS.limit, JOBS.waiting, JOBS.max_pending, JOBS.timeout), mention_author=False)


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
    blocks = []
    for name, text in items:
        try:
            rep = await _job(analyze, text)
        except JobError as error:
            await _file_error(ctx, name, error)
            continue
        if rep["engines"]:
            top = rep["engines"][0]
            head = "**%s** - %s (match score %d/100)" % (name[:120], top[0], top[1])
            if len(rep["engines"]) > 1:
                head += " (also: %s)" % ", ".join("%s %d/100" % (e[0], e[1]) for e in rep["engines"][1:3])
            if rep["ambiguous"]:
                head += " — overlapping signatures"
            why = "because: " + ", ".join(top[2][:3])
            hint = "try `.%s` or `.deobf`" % top[0]
        else:
            head, why, hint = "**%s** - no known signature" % name, "", "try `.deobf`, it still runs and records the script"
        tags = ", ".join(t[0] for t in rep["techniques"]) or "none seen"
        blocks.append("\n".join(x for x in (head, "obfuscation score %d/100 | techniques: %s" % (rep["score"], tags), why, hint) if x))
    if blocks:
        await _reply_chunks(ctx, "\n\n".join(blocks))


@bot.command(name="l")
async def luau_cmd(ctx, *, arg=""):
    items = await gather_inputs(ctx, arg)
    async with ctx.typing():
        async for name, _, result in _batch(items, trace_behavior):
            if isinstance(result, Exception):
                await _file_error(ctx, name, result)
                continue
            text, _run = result
            await ctx.reply("behaviour trace: **%s**" % name[:120], file=_file(text, "trace_" + os.path.basename(name)), mention_author=False)


class FetchView(JobView):
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
    text = "A3K Deobfuscator — %d detector profiles\n" % len(ENGINES)
    text += "\n".join(lines) + "\n\nqueue: %d running, %d waiting, limit %d" % (JOBS.active, JOBS.waiting, JOBS.limit)
    await _reply_chunks(ctx, text)


HELP = """**A3K Deobfuscator**
**Deobfuscation**
`.deobf` - detect + pick engine (unified panel)
`.static` - decode literal loaders without running the script
`.report` - download JSON evidence, layer history, results, and findings
`.status` - running jobs, queue, and limits
{engines}
`.detect` - identify the obfuscator (with confidence)
`.l` - reconstruct observable Luau behaviour
**Tools**
`.get <url>` - fetch a script, then Deobfuscate or Env Log it
`.envlog` - executor-side capture loader (or analyse uploaded `loadstring*.txt`)
`.engines` - detector profiles + queue status
`.help` - this message
**Safety**
every result is scanned for item-stealer / data-exfil chunks; if found you get a Remove logger button.
attach up to 10 .lua/.luau/.txt files, paste code, give a url, or reply to a sample. Jobs use a bounded queue.
Detection uses heuristic signatures; recovery is best effort. VM output may be a behavior trace."""


async def _reply_chunks(ctx, text):
    """Keep help/catalog replies below Discord's 2,000-character limit."""
    chunk = ""
    for line in text.splitlines(keepends=True):
        if chunk and len(chunk) + len(line) > 1900:
            await ctx.reply(chunk, mention_author=False)
            chunk = ""
        while len(line) > 1900:
            await ctx.reply(line[:1900], mention_author=False)
            line = line[1900:]
        chunk += line
    if chunk:
        await ctx.reply(chunk, mention_author=False)


@bot.command(name="help")
async def help_cmd(ctx):
    await _reply_chunks(ctx, HELP.format(engines=" · ".join("`.%s`" % n for n in ENGINE_NAMES)))


@bot.event
async def on_command_error(ctx, err):
    err = getattr(err, "original", err)
    if isinstance(err, commands.CommandNotFound):
        return
    if isinstance(err, (commands.UserInputError, commands.MissingRequiredArgument, JobError)):
        await ctx.reply(str(err), mention_author=False)
        return
    await ctx.reply("failed: %s" % type(err).__name__, mention_author=False)
    raise err


def main():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("set DISCORD_TOKEN (bot token from the discord developer portal)")
    try:
        bot.run(token)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected DISCORD_TOKEN. Update the token in Railway Variables and redeploy.") from None
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Enable Message Content Intent in Discord Developer Portal > Bot, then restart.") from None


if __name__ == "__main__":
    main()
