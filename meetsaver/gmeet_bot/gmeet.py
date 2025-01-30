import asyncio
from asyncio import subprocess
from datetime import timedelta
from os import getenv
from time import time

import nodriver as uc

from .. import utils
from . import exceptions as ex

logger = utils.logger.setup_logger(__name__)

VIDEO = "output.mp4"


def SCREENSHOT() -> str:
    return f"screenshot_{int(time())}.png"


CMD_FFMPEG = f"ffmpeg -y -loglevel warning -framerate 30 \
    -f x11grab -i :0 -f pulse -i virtual_sink.monitor -ac 2 -b:a 192k {VIDEO}"
CMD_PULSE = "pulseaudio -D --system=false --exit-idle-time=-1 --disallow-exit --log-level=debug \
    && pactl load-module module-null-sink sink_name=virtual_sink \
    && pactl set-default-sink virtual_sink"
TIMEOUT = 5


class GMeet:
    __instance = None

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
            cls.__instance.__init()
        return cls.__instance

    def __init(self):
        print("Gmeet initializated.")
        self.__is_pulse_ready = False
        self.__start_time = 0
        self.__browser: uc.Browser = None
        self.__meet_page: uc.Tab = None

    async def __setup_browser(self):
        self.__browser = await uc.start(
            browser_args=[
                f"--window-size={getenv("SCREEN_WIDTH")},{getenv("SCREEN_HEIGHT")}",
                "--incognito",
            ]
        )

    async def __google_sign_in(self):
        logger.info("Signing in google account...")
        page = await self.__browser.get("https://accounts.google.com")
        await self.__browser.wait(3)
        email_field = await page.select("input[type=email]")
        await email_field.send_keys(getenv("GMAIL"))
        await self.__browser.wait(2)
        next_btn = await page.find("next")
        await next_btn.mouse_click()
        await self.__browser.wait(3)
        password_field = await page.select("input[type=password]")
        await password_field.send_keys(getenv("GPASS"))
        next_btn = await page.find("next")
        await next_btn.mouse_click()
        await self.__browser.wait(5)
        logger.info("Completed signing in google account.")

    async def __run_cmd(self, command, on_background=False):
        process = await asyncio.create_subprocess_shell(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=asyncio.subprocess.PIPE
        )
        if on_background:
            logger.info(f"Cmd started with PID: {process.pid}")
            return process
        # Wait for the process to complete
        stdout, stderr = await process.communicate()
        return stdout, stderr

    @property
    def is_running(self):
        return not (self.__browser is None)

    async def __run_pulse(self):
        logger.info("Running pulse...")
        try:
            stdout, stderr = await asyncio.wait_for(self.__run_cmd(CMD_PULSE), timeout=TIMEOUT)
            logger.debug(stderr)
        except asyncio.TimeoutError:
            logger.error("Can't run cmd. Exiting it...")
            raise ex.ModuleException("pulse")
        self.__is_pulse_ready = True

    @property
    def recording_time(self) -> None | str:
        if not self.__start_time:
            return None
        return str(timedelta(seconds=time() - self.__start_time)).split(".")[0]

    async def get_screenshot(self) -> None | str:
        logger.info("Getting screenshot...")
        if not self.__meet_page:
            return None
        return await self.__meet_page.save_screenshot(SCREENSHOT())

    async def __run_recording(self):
        logger.info("Start recording...")
        self.__start_time = time()
        ffmpeg = await self.__run_cmd(CMD_FFMPEG, True)
        await asyncio.sleep(10)
        logger.info("Stoped recording.")
        self.__start_time = 0
        return ffmpeg

    async def record_meet(self, meet_link: str) -> str:
        if self.is_running:
            raise ex.AlreadyRunException()
        logger.info(f"Recoring for link: {meet_link}")
        if not self.__is_pulse_ready:
            await self.__run_pulse()

        await self.__setup_browser()
        await self.__google_sign_in()
        self.__meet_page = await self.__browser.get(meet_link)
        await self.__browser.wait(5)
        next_btn = await self.__meet_page.find("join now")
        await next_btn.mouse_click()
        await self.__browser.wait(5)

        ffmpeg = await self.__run_recording()

        exit_btn = await self.__meet_page.find_element_by_text("leave call")
        await exit_btn.mouse_click()
        await self.__browser.wait(2)
        self.__browser.stop()
        self.__meet_page = None

        try:
            ffmpeg.stdin.write(b"q")
            logger.debug("FFmpeg terminated. Waiting...")
            await ffmpeg.stdin.drain()
            stdout, stderr = await asyncio.wait_for(ffmpeg.communicate(), timeout=TIMEOUT)
            logger.debug(stderr)
            return VIDEO
        except asyncio.TimeoutError:
            logger.error("Can't terminate ffmpeg. Killing it...")
            ffmpeg.kill()
            raise ex.ModuleException("ffmpeg")
        finally:
            self.__browser = None
