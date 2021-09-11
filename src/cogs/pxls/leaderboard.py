import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import plotly.graph_objects as go
from utils.image.image_utils import hex_str_to_int
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option, create_choice

from utils.setup import db_stats, db_users, GUILD_IDS
from utils.time_converter import *
from utils.arguments_parser import parse_leaderboard_args
from utils.discord_utils import format_number, image_to_file
from utils.table_to_image import table_to_image
from utils.plot_utils import fig2img, get_theme, hex_to_rgba_string
from utils.pxls.cooldown import get_best_possible
from cogs.pxls.speed import get_stats_graph
class PxlsLeaderboard(commands.Cog, name="Pxls Leaderboard"):

    def __init__(self, client):
        self.client = client

    ### Discord commands ###
    @cog_ext.cog_slash(
        name="leaderboard",
        description="Show the all-time or canvas leaderboard.",
        guild_ids=GUILD_IDS,
        options=[
        create_option(
            name="username",
            description="Center the leaderboard on this user (`!` = your set username).",
            option_type=3,
            required=False
        ),
        create_option(
            name="canvas",
            description="To get the canvas leaderboard.",
            option_type=5,
            required=False
        ),
        create_option(
            name="lines",
            description="The number of lines to show (between 1 and 40).",
            option_type=4,
            required=False
        ),
        create_option(
            name="graph",
            description="To show a progress graph for each user in the leaderboard.",
            option_type=5,
            required=False
        ),
        create_option(
            name="bars",
            description="To show a bar graph of the current leaderboard",
            option_type=5,
            required=False
        ),
        create_option(
            name="ranks",
            description="Show the leaderboard between 2 ranks (format: ?-?)",
            option_type=3,
            required=False
        ),
        create_option(
            name="last",
            description="Show the leaderboard in the last x year/month/week/day/hour/minute/second. (format: ?y?mo?w?d?h?m?s)",
            option_type=3,
            required=False
        ),
        create_option(
            name="before",
            description="To show the leaderboard before a specific date (format: YYYY-mm-dd HH:MM)",
            option_type=3,
            required=False
        ),
        create_option(
            name="after",
            description="To show the leaderboard after a specific date (format: YYYY-mm-dd HH:MM)",
            option_type=3,
            required=False
        )]
    )
    async def _speed(self,ctx:SlashContext,username=None,canvas=False,lines=None,
        graph=None,bars=False,ranks=None,last=None,before=None,after=None):
        await ctx.defer()
        args = ()
        if username:
            args += tuple(username.split(" "))
        if canvas:
            args += ("-canvas",)
        if lines:
            args += ("-lines",str(lines))
        if graph:
            args += ("-graph",)
        if bars:
            args += ("-bars",)
        if ranks:
            args += ("-ranks",ranks)
        if last:
            args += ("-last",last)
        if before:
            args += ("-before",) + tuple(before.split(" "))
        if after:
            args += ("-after",) + tuple(after.split(" "))
        await self.leaderboard(ctx,*args)

    @commands.command(
        name = "leaderboard",
        usage = "[username] [-canvas] [-lines <number>] [-graph] [-bars] [-ranks ?-?] [-last ?y?m?w?d?h?m?s] [-before <date time>] [-after <date time>]",
        description = "Show the all-time or canvas leaderboard.",
        aliases=["ldb"],
        help = """- `[username]`: center the leaderboard on this user (`!` = your set username)
                  - `[-canvas|-c]`: to get the canvas leaderboard
                  - `[-lines <number>]`: number of lines to show, must be less than 40 (default 15)
                  - `[-graph|-g]`: show a progress graph for each user in the leaderboard
                  - `[-bars|-b]`: show a bar graph of the leaderboard
                  - `[-ranks ?-?]`: show the leaderboard between 2 ranks
                  - `[-last ?y?mo?w?d?h?m?s]` Show the leaderboard in the last x years/months/weeks/days/hour/min/s""" 
        )
    async def p_leaderboard(self,ctx,*args):
        async with ctx.typing():
            await self.leaderboard(ctx,*args)

    async def leaderboard(self,ctx,*args):
        ''' Shows the pxls.space leaderboard '''

        try:
            param = parse_leaderboard_args(args)
        except ValueError as e:
            return await ctx.send(f'❌ {e}')
        nb_line = int(param["lines"])
        canvas_opt = param["canvas"]
        speed_opt = False
        sort_opt = None
        last_opt = param["last"]
        bars_opt = param["bars"]
        graph_opt = param["graph"]
        ranks_opt = param["ranks"]

        # get the user theme
        discord_user = await db_users.get_discord_user(ctx.author.id)
        current_user_theme = discord_user["color"] or "default"
        theme = get_theme(current_user_theme)

        # get the linked username if "!" is in the names list
        username = param["names"]
        if "!" in username:
            pxls_user_id = discord_user["pxls_user_id"]
            if pxls_user_id == None:
                usage_text = f"(You can set your default username with `{ctx.prefix}setname <username>`)"
                return await ctx.send("❌ You need to have a set username to use `!`.\n" + usage_text)
            name = await db_users.get_pxls_user_name(pxls_user_id)
            username = [name if u=="!" else u for u in username]

        # if a time value is given, we will show the leaderboard during this time
        if param["before"] or param["after"] or last_opt:
            speed_opt = True
            sort_opt = "speed"
            if param["before"] == None and param["after"] == None:
                date = last_opt
                input_time = str_to_td(date)
                if not input_time:
                    return await ctx.send(f"❌ Invalid `last` parameter, format must be `?y?mo?w?d?h?m?s`.")
                input_time = input_time + timedelta(minutes=1)
                date2 = datetime.now(timezone.utc)
                date1 = round_minutes_down(datetime.now(timezone.utc) - input_time)
            else:
                last_opt = None
                date1 = param["after"] or datetime(1900,1,1,0,0,0)
                date2 = param["before"] or datetime.now(timezone.utc)
        else:
            date1 = datetime.now(timezone.utc)
            date2 = datetime.now(timezone.utc)

        # check on sort arg
        if not sort_opt:
            sort = 'canvas' if canvas_opt else 'alltime'
        else:
            sort = sort_opt
            #canvas_opt = "canvas" # only get the canvas stats when sorting by speed

        # fetch the leaderboard from the database
        canvas,last_date, datetime1, datetime2, ldb = await db_stats.get_pixels_placed_between(date1,date2,canvas_opt,sort)
        # change the canvas opt if sort by speed on time frame on the current canvas
        canvas_opt = canvas

        # check that we can actually calculate the speed
        if speed_opt and datetime1 == datetime2:
            return await ctx.send("❌ The time frame given is too short.")

        # trim the leaderboard to only get the lines asked
        if ranks_opt:
            (rank_low, rank_high) = ranks_opt
            username = []
            try:
                ldb = ldb[rank_low-1:rank_high]
            except IndexError:
                return await ctx.send("❌ Can't find values between these ranks in the leaderboard.")

        elif username:
            # looking for the index and pixel count of the given user
            name_index = None
            for index,line in enumerate(ldb):
                name = list(line)[1]
                if speed_opt:
                    pixels = list(line)[3]
                else:
                    pixels = list(line)[2]
                if name == username[0]:
                    name_index = index
                    user_pixels = pixels
                    break
            if name_index == None:
                return await ctx.send("❌ User not found")

            # calucluate the indexes around the user
            min_idx = max(0,(name_index-round(nb_line/2)))
            max_idx = min(len(ldb),name_index+round(nb_line/2)+1)
            # if one of the idx hits the limit, we change the other idx to show more lines
            if min_idx == 0:
                max_idx = min_idx + nb_line
            if max_idx == len(ldb):
                min_idx = max_idx - nb_line
            ldb = ldb[min_idx:max_idx]
        else:
            ldb = ldb[0:nb_line]

        # build the header of the leaderboard
        column_names = ["Rank","Username","Pixels"]
        alignments2 = ['center','center','right']
        if username:
            column_names.append("Diff")
            alignments2.append('left')
        if speed_opt:
            column_names.append("Speed")
            alignments2.append('right')

        # build the content of the leaderboard
        res_ldb = []
        for i in range(len(ldb)):
            res_ldb.append([])
            # add the rank
            rank = ldb[i][0]
            if rank > 1000:
                rank = ">1000"
            res_ldb[i].append(rank)

            # add the name
            res_ldb[i].append(ldb[i][1])

            # add the pixel count
            if speed_opt:
                res_ldb[i].append(ldb[i][3])
            else:
                res_ldb[i].append(ldb[i][2])
            # format the number
            if res_ldb[i][2] != None:
                res_ldb[i][2] = format_number(res_ldb[i][2])
            else:
                res_ldb[i][2] = "???"

            # add the diff values
            if username:
                try:
                    if speed_opt:
                        diff = user_pixels-ldb[i][3]
                    else:
                        diff = user_pixels-ldb[i][2]
                    diff = f'{int(diff):,}'.replace(","," ") # convert to string
                    res_ldb[i].append(diff)
                except:
                    res_ldb[i].append("???")

            # add the speed
            if speed_opt:
                diff_pixels = ldb[i][3] or 0
                diff_time = (datetime2 - datetime1)
                nb_hours = diff_time/timedelta(hours=1)
                speed = diff_pixels/nb_hours
                # show the speed in pixel/day if the time frame is more than a day
                if nb_hours > 24:
                    speed = speed*24
                    res_ldb[i].append(f'{round(speed,1)} px/d')
                else:
                    res_ldb[i].append(f'{round(speed,1)} px/h')

        # create the image with the leaderboard data
        colors = None
        if graph_opt:
            colors = theme.get_palette(len(res_ldb))
        elif username:
            colors = []
            for e in res_ldb:
                if e[1] == username[0]:
                    colors.append(theme.get_palette(1)[0])
                else:
                    colors.append(None)
        img = await self.client.loop.run_in_executor(
            None,table_to_image,res_ldb,column_names,alignments2,colors,theme)

        # make title and embed header
        text = ""
        if speed_opt:
            past_time = datetime1
            recent_time = datetime2
            diff_time = round_minutes_down(recent_time)-round_minutes_down(past_time)
            diff_time_str = td_format(diff_time)
            if last_opt:
                title = "Leaderboard of the last {}".format(
                    diff_time_str[2:] if (diff_time_str.startswith("1 ") and not ',' in diff_time_str)
                    else diff_time_str)
            else:
                title = "Leaderboard"
                text += "• Between {} and {}\n({})\n".format(
                    format_datetime(past_time),
                    format_datetime(recent_time),
                    td_format(diff_time)
                )
            # calculate the best possbile amount in the time frame
            best_possible,average_cooldown = await get_best_possible(datetime1,datetime2)
            text += f"• Average cooldown: `{round(average_cooldown,2)}` seconds\n"
            text += f"• Best possible (without stack): ~`{format_number(best_possible)}` pixels.\n"

        elif canvas_opt:
            title = "Canvas Leaderboard"
        else:
            title = "All-time Leaderboard"

        if not(param["before"] or param["after"]):
            text +=  f"• Last updated: {format_datetime(last_date,'R')}"

        # make the progress graph
        if graph_opt:
            name_list = [u[1] for u in res_ldb]
            if last_opt == None:
                dt1 = datetime(1900,1,1)
                dt2 = datetime.utcnow() 
            else:
                dt1 = datetime1
                dt2 = datetime2
            stats_dt1, stats_dt2, stats_history = await db_stats.get_stats_history(name_list,dt1,dt2,canvas_opt)
            stats = []
            for user in stats_history:
                name = user[0]
                data = user[1]
                data_dates = [stat["datetime"] for stat in data]
                if last_opt != None:
                    # substract the first value to each value so they start at 0
                    data_pixels = [stat["pixels"] - data[0]["pixels"] for stat in data]
                else:
                    data_pixels = [stat["pixels"] for stat in data]
                stats.append([name,data_dates,data_pixels])
            stats.sort(key = lambda x:x[2][-1],reverse=True)
            graph_img = await self.client.loop.run_in_executor(None,get_stats_graph,stats,"",theme,discord_user["timezone"])
            graph_file = image_to_file(graph_img,"graph.png")

        # make the bars graph
        if bars_opt:
            data = [int(user[2].replace(" ","")) if user[2] != "???" else None for user in res_ldb]
            theme_colors = theme.get_palette(1)
            if username:
                names = [(f"<span style='color:{theme_colors[0]};'>{user[1]}</span>" if user[1] == username[0] else user[1]) for user in res_ldb]
                colors = [(theme_colors[0] if user[1] == username[0] else theme.off_color) for user in res_ldb]
            else:
                names = [user[1] for user in res_ldb]
                colors = [theme_colors[0]]*len(res_ldb)
            fig = self.make_bars(names,data,title,theme,colors)

            bars_img = fig2img(fig)
            bars_file = image_to_file(bars_img,"bar_chart.png")

        # create a discord embed
        emb = discord.Embed(color=hex_str_to_int(theme.get_palette(1)[0]),
            title=title,description=text)
        file = image_to_file(img,"leaderboard.png",emb)

        # send graph and embed
        await ctx.send(embed=emb,file=file)
        # send graph image
        if graph_opt:
            await ctx.channel.send(file=graph_file)
        if bars_opt:
            await ctx.channel.send(file=bars_file)

    @staticmethod
    def make_bars(users,pixels,title,theme,colors=None):
        if colors == None:
            colors = theme.get_palette(len(users))
        # create the graph and style
        fig = go.Figure(layout=theme.get_layout(with_annotation=False))
        fig.update_yaxes(rangemode='tozero')
        fig.update_xaxes(tickmode='linear')
        fig.update_layout(annotations=[])
        fig.update()
        # the title displays the user if there is only 1 in the user_list
        fig.update_layout(title="<span style='color:{};'>{}</span>".format(
            theme.get_palette(1)[0],
            title))

        text = ['<span style = "text-shadow:\
                        -{2}px -{2}px 0 {0},\
                        {2}px -{2}px 0 {0},\
                        -{2}px {2}px 0 {0},\
                        {2}px {2}px 0 {0},\
                        0px {2}px 0px {0},\
                        {2}px 0px 0px {0},\
                        -{2}px 0px 0px {0},\
                        0px -{2}px 0px {0};">{1}</span>'.format(theme.background_color,pixel,2) for pixel in pixels]

        # trace the user data
        if theme.has_underglow == True:
            # different style if the theme has underglow
            fig.add_trace(
                go.Bar(
                    x = users,
                    y = pixels,
                    text = text,
                    textposition = 'outside',
                    marker_color = [hex_to_rgba_string(color,0.3) for color in colors],
                    marker_line_color = colors,
                    marker_line_width=2.5,
                    textfont = dict(color=colors, size=40),
                    cliponaxis = False
                )
            )
        else:
            fig.add_trace(
                go.Bar(
                    x = users,
                    y = pixels,
                    # add an outline of the bg color to the text
                    text = text,
                    textposition = 'outside',
                    marker = dict(color=colors, opacity=0.95),
                    textfont = dict(color=colors, size=40),
                    cliponaxis = False
                )
            )
        return fig

def setup(client):
    client.add_cog(PxlsLeaderboard(client))