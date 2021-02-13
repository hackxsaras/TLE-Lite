import bisect
import collections
import datetime as dt
import time
import itertools
import math
from typing import List

import discord
import numpy as np
import pandas as pd
import seaborn as sns
from discord.ext import commands
from matplotlib import pyplot as plt
from matplotlib import patches as patches
from matplotlib import lines as mlines
from matplotlib import dates as mdates

import TLEconstants
from util import codeforces_api as cf
from util import codeforces_common as cf_common
from util import discord_common
from util import graph_common as gc

pd.plotting.register_matplotlib_converters()

# A user is considered active if the duration since his last contest is not more than this
CONTEST_ACTIVE_TIME_CUTOFF = 90 * 24 * 60 * 60 # 90 days


class GraphCogError(commands.CommandError):
    pass

def nice_sub_type(types):
    nice_map = {'CONTESTANT':'Contest: {}',
                'OUT_OF_COMPETITION':'Unofficial: {}',
                'VIRTUAL':'Virtual: {}',
                'PRACTICE':'Practice: {}'}
    return [nice_map[t] for t in types]

def _plot_rating(resp, mark='o'):

    for rating_changes in resp:
        ratings, times = [], []
        for rating_change in rating_changes:
            ratings.append(rating_change.newRating)
            times.append(dt.datetime.fromtimestamp(rating_change.ratingUpdateTimeSeconds))

        plt.plot(times,
                 ratings,
                 linestyle='-',
                 marker=mark,
                 markersize=3,
                 markerfacecolor='white',
                 markeredgewidth=0.5)

    gc.plot_rating_bg(cf.RATED_RANKS)
    plt.gcf().autofmt_xdate()

def _classify_submissions(submissions):
    solved_by_type = {sub_type: [] for sub_type in cf.Party.PARTICIPANT_TYPES}
    for submission in submissions:
        solved_by_type[submission.author.participantType].append(submission)
    return solved_by_type


def _plot_scatter(regular, practice, virtual, point_size):
    for contest in [practice, regular, virtual]:
        if contest:
            times, ratings = zip(*contest)
            plt.scatter(times, ratings, zorder=10, s=point_size)


def _running_mean(x, bin_size):
    n = len(x)

    cum_sum = [0] * (n + 1)
    for i in range(n):
        cum_sum[i + 1] = x[i] + cum_sum[i]

    res = [0] * (n - bin_size + 1)
    for i in range(bin_size, n + 1):
        res[i - bin_size] = (cum_sum[i] - cum_sum[i - bin_size]) / bin_size

    return res





def _plot_average(practice, bin_size, label: str = ''):
    if len(practice) > bin_size:
        sub_times, ratings = map(list, zip(*practice))

        sub_timestamps = [sub_time.timestamp() for sub_time in sub_times]
        mean_sub_timestamps = _running_mean(sub_timestamps, bin_size)
        mean_sub_times = [dt.datetime.fromtimestamp(timestamp) for timestamp in mean_sub_timestamps]
        mean_ratings = _running_mean(ratings, bin_size)

        plt.plot(mean_sub_times,
                 mean_ratings,
                 linestyle='-',
                 marker='',
                 markerfacecolor='white',
                 markeredgewidth=0.5,
                 label=label)


class Graphs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.converter = commands.MemberConverter()

    @commands.group(brief='Graphs for analyzing Codeforces activity',
                    invoke_without_command=True)
    async def plot(self, ctx):
        """Plot various graphs. Wherever Codeforces handles are accepted it is possible to
        use a server member's name instead by prefixing it with '!',
        for name with spaces use "!name with spaces" (with quotes)."""
        await ctx.send_help('plot')

    @plot.command(brief='Plot Codeforces rating graph', usage='[+zoom] [+peak] [handles...] [d>=[[dd]mm]yyyy] [d<[[dd]mm]yyyy]')
    async def rating(self, ctx, *args: str):
        """Plots Codeforces rating graph for the handles provided."""

        (zoom, peak), args = cf_common.filter_flags(args, ['+zoom' , '+peak'])
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handles = args or ('!' + str(ctx.author),)
        handles = await cf_common.resolve_handles(ctx, self.converter, handles)
        resp = [await cf.user.rating(handle=handle) for handle in handles]
        resp = [filt.filter_rating_changes(rating_changes) for rating_changes in resp]

        if not any(resp):
            handles_str = ', '.join(f'`{handle}`' for handle in handles)
            if len(handles) == 1:
                message = f'User {handles_str} is not rated'
            else:
                message = f'None of the given users {handles_str} are rated'
            raise GraphCogError(message)

        def max_prefix(user):
            max_rate = 0
            res = []
            for data in user:
                old_rating = data.oldRating
                if old_rating == 0:
                    old_rating = 1500
                if data.newRating - old_rating >= 0 and data.newRating >= max_rate:
                    max_rate = data.newRating
                    res.append(data)
            return(res)

        if peak:
            resp = [max_prefix(user) for user in resp]

        plt.clf()
        plt.axes().set_prop_cycle(gc.rating_color_cycler)
        _plot_rating(resp)
        current_ratings = [rating_changes[-1].newRating if rating_changes else 'Unrated' for rating_changes in resp]
        labels = [gc.StrWrap(f'{handle} ({rating})') for handle, rating in zip(handles, current_ratings)]
        plt.legend(labels, loc='upper left')

        if not zoom:
            min_rating = 1100
            max_rating = 1800
            for rating_changes in resp:
                for rating in rating_changes:
                    min_rating = min(min_rating, rating.newRating)
                    max_rating = max(max_rating, rating.newRating)
            plt.ylim(min_rating - 100, max_rating + 200)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Rating graph on Codeforces')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)


    @plot.command(brief="Show histogram of solved problems' rating on CF",
                  usage='[handles] [+practice] [+contest] [+virtual] [+outof] [+team] [+tag..] [r>=rating] [r<=rating] [d>=[[dd]mm]yyyy] [d<[[dd]mm]yyyy] [c+marker..] [i+index..]')
    async def solved(self, ctx, *args: str):
        """Shows a histogram of solved problems' rating on Codeforces for the handles provided.
        e.g. ;plot solved meooow +contest +virtual +outof +dp"""
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handles = args or ('!' + str(ctx.author),)
        handles = await cf_common.resolve_handles(ctx, self.converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        if not any(all_solved_subs):
            raise GraphCogError(f'There are no problems within the specified parameters.')

        plt.clf()
        plt.xlabel('Problem rating')
        plt.ylabel('Number solved')
        if len(handles) == 1:
            # Display solved problem separately by type for a single user.
            handle, solved_by_type = handles[0], _classify_submissions(all_solved_subs[0])
            all_ratings = [[sub.problem.rating for sub in solved_by_type[sub_type]]
                           for sub_type in filt.types]

            nice_names = nice_sub_type(filt.types)
            labels = [name.format(len(ratings)) for name, ratings in zip(nice_names, all_ratings)]

            step = 100
            # shift the range to center the text
            hist_bins = list(range(filt.rlo - step // 2, filt.rhi + step // 2 + 1, step))
            plt.hist(all_ratings, stacked=True, bins=hist_bins, label=labels)
            total = sum(map(len, all_ratings))
            plt.legend(title=f'{handle}: {total}', title_fontsize=plt.rcParams['legend.fontsize'],
                       loc='upper right')

        else:
            all_ratings = [[sub.problem.rating for sub in solved_subs]
                           for solved_subs in all_solved_subs]
            labels = [gc.StrWrap(f'{handle}: {len(ratings)}')
                      for handle, ratings in zip(handles, all_ratings)]

            step = 200 if filt.rhi - filt.rlo > 3000 // len(handles) else 100
            hist_bins = list(range(filt.rlo - step // 2, filt.rhi + step // 2 + 1, step))
            plt.hist(all_ratings, bins=hist_bins)
            plt.legend(labels, loc='upper right')

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Histogram of problems solved on Codeforces')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)

    @plot.command(brief='Show histogram of solved problems on CF over time',
                  usage='[handles] [+practice] [+contest] [+virtual] [+outof] [+team] [+tag..] [r>=rating] [r<=rating] [d>=[[dd]mm]yyyy] [d<[[dd]mm]yyyy] [phase_days=] [c+marker..] [i+index..]')
    async def hist(self, ctx, *args: str):
        """Shows the histogram of problems solved on Codeforces over time for the handles provided"""
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        phase_days = 1
        handles = []
        for arg in args:
            if arg[0:11] == 'phase_days=':
                phase_days = int(arg[11:])
            else:
                handles.append(arg)

        if phase_days < 1:
            raise GraphCogError('Invalid parameters')
        phase_time = dt.timedelta(days=phase_days)

        handles = handles or ['!' + str(ctx.author)]
        handles = await cf_common.resolve_handles(ctx, self.converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        if not any(all_solved_subs):
            raise GraphCogError(f'There are no problems within the specified parameters.')

        plt.clf()
        plt.xlabel('Time')
        plt.ylabel('Number solved')
        if len(handles) == 1:
            handle, solved_by_type = handles[0], _classify_submissions(all_solved_subs[0])
            all_times = [[dt.datetime.fromtimestamp(sub.creationTimeSeconds) for sub in solved_by_type[sub_type]]
                         for sub_type in filt.types]

            nice_names = nice_sub_type(filt.types)
            labels = [name.format(len(times)) for name, times in zip(nice_names, all_times)]

            dlo = min(itertools.chain.from_iterable(all_times)).date()
            dhi = min(dt.datetime.today() + dt.timedelta(days=1), dt.datetime.fromtimestamp(filt.dhi)).date()
            phase_cnt = math.ceil((dhi - dlo) / phase_time)
            plt.hist(
                all_times,
                stacked=True,
                label=labels,
                range=(dhi - phase_cnt * phase_time, dhi),
                bins=min(40, phase_cnt))

            total = sum(map(len, all_times))
            plt.legend(title=f'{handle}: {total}', title_fontsize=plt.rcParams['legend.fontsize'])
        else:
            all_times = [[dt.datetime.fromtimestamp(sub.creationTimeSeconds) for sub in solved_subs]
                         for solved_subs in all_solved_subs]

            # NOTE: matplotlib ignores labels that begin with _
            # https://matplotlib.org/api/pyplot_api.html#matplotlib.pyplot.legend
            # Add zero-width space to work around this
            labels = [gc.StrWrap(f'{handle}: {len(times)}')
                      for handle, times in zip(handles, all_times)]

            dlo = min(itertools.chain.from_iterable(all_times)).date()
            dhi = min(dt.datetime.today() + dt.timedelta(days=1), dt.datetime.fromtimestamp(filt.dhi)).date()
            phase_cnt = math.ceil((dhi - dlo) / phase_time)
            plt.hist(
                all_times,
                range=(dhi - phase_cnt * phase_time, dhi),
                bins=min(40 // len(handles), phase_cnt))
            plt.legend(labels)

        # NOTE: In case of nested list, matplotlib decides type using 1st sublist,
        # it assumes float when 1st sublist is empty.
        # Hence explicitly assigning locator and formatter is must here.
        locator = mdates.AutoDateLocator()
        plt.gca().xaxis.set_major_locator(locator)
        plt.gca().xaxis.set_major_formatter(mdates.AutoDateFormatter(locator))

        plt.gcf().autofmt_xdate()
        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Histogram of number of solved problems over time')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)

    @plot.command(brief='Plot count of solved CF problems over time',
                  usage='[handles] [+practice] [+contest] [+virtual] [+outof] [+team] [+tag..] [r>=rating] [r<=rating] [d>=[[dd]mm]yyyy] [d<[[dd]mm]yyyy] [c+marker..] [i+index..]')
    async def curve(self, ctx, *args: str):
        """Plots the count of problems solved over time on Codeforces for the handles provided."""
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handles = args or ('!' + str(ctx.author),)
        handles = await cf_common.resolve_handles(ctx, self.converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        if not any(all_solved_subs):
            raise GraphCogError(f'There are no problems within the specified parameters.')

        plt.clf()
        plt.xlabel('Time')
        plt.ylabel('Cumulative solve count')

        all_times = [[dt.datetime.fromtimestamp(sub.creationTimeSeconds) for sub in solved_subs]
                     for solved_subs in all_solved_subs]
        for times in all_times:
            cumulative_solve_count = list(range(1, len(times)+1)) + [len(times)]
            timestretched = times + [min(dt.datetime.now(), dt.datetime.fromtimestamp(filt.dhi))]
            plt.plot(timestretched, cumulative_solve_count)

        labels = [gc.StrWrap(f'{handle}: {len(times)}')
                  for handle, times in zip(handles, all_times)]

        plt.legend(labels)

        plt.gcf().autofmt_xdate()
        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Curve of number of solved problems over time')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)

    @plot.command(brief='Show history of problems solved by rating',
                  aliases=['chilli'], usage='[handle] [+practice] [+contest] [+virtual] [+outof] [+team] [+tag..] [r>=rating] [r<=rating] [d>=[[dd]mm]yyyy] [d<[[dd]mm]yyyy] [b=10] [s=3] [c+marker..] [i+index..] [+nolegend]')
    async def scatter(self, ctx, *args):
        """Plot Codeforces rating overlaid on a scatter plot of problems solved.
        Also plots a running average of ratings of problems solved in practice."""
        (nolegend,), args = cf_common.filter_flags(args, ['+nolegend'])
        legend, = cf_common.negate_flags(nolegend)
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handle, bin_size, point_size = None, 10, 3
        for arg in args:
            if arg[0:2] == 'b=':
                bin_size = int(arg[2:])
            elif arg[0:2] == 's=':
                point_size = int(arg[2:])
            else:
                if handle:
                    raise GraphCogError('Only one handle allowed.')
                handle = arg

        if bin_size < 1 or point_size < 1 or point_size > 100:
            raise GraphCogError('Invalid parameters')

        handle = handle or '!' + str(ctx.author)
        handle, = await cf_common.resolve_handles(ctx, self.converter, (handle,))
        rating_resp = [await cf.user.rating(handle=handle)]
        rating_resp = [filt.filter_rating_changes(rating_changes) for rating_changes in rating_resp]
        submissions = filt.filter_subs(await cf.user.status(handle=handle))

        def extract_time_and_rating(submissions):
            return [(dt.datetime.fromtimestamp(sub.creationTimeSeconds), sub.problem.rating)
                    for sub in submissions]

        if not any(submissions):
            raise GraphCogError(f'No submissions for user `{handle}`')

        solved_by_type = _classify_submissions(submissions)
        regular = extract_time_and_rating(solved_by_type['CONTESTANT'] +
                                          solved_by_type['OUT_OF_COMPETITION'])
        practice = extract_time_and_rating(solved_by_type['PRACTICE'])
        virtual = extract_time_and_rating(solved_by_type['VIRTUAL'])

        plt.clf()
        _plot_scatter(regular, practice, virtual, point_size)
        labels = []
        if practice:
            labels.append('Practice')
        if regular:
            labels.append('Regular')
        if virtual:
            labels.append('Virtual')
        if legend:
            plt.legend(labels, loc='upper left')
        _plot_average(practice, bin_size)
        _plot_rating(rating_resp, mark='')

        # zoom
        ymin, ymax = plt.gca().get_ylim()
        plt.ylim(max(ymin, filt.rlo - 100), min(ymax, filt.rhi + 100))

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title=f'Rating vs solved problem rating for {handle}')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)

    async def _rating_hist(self, ctx, ratings, mode, binsize, title):
        if mode not in ('log', 'normal'):
            raise GraphCogError('Mode should be either `log` or `normal`')

        ratings = [r for r in ratings if r >= 0]
        assert ratings, 'Cannot histogram plot empty list of ratings'

        assert 100%binsize == 0 # because bins is semi-hardcoded
        bins = 39*100//binsize

        colors = []
        low, high = 0, binsize * bins
        for rank in cf.RATED_RANKS:
            for r in range(max(rank.low, low), min(rank.high, high), binsize):
                colors.append('#' + '%06x' % rank.color_embed)
        assert len(colors) == bins, f'Expected {bins} colors, got {len(colors)}'

        height = [0] * bins
        for r in ratings:
            height[r // binsize] += 1

        csum = 0
        cent = [0]
        users = sum(height)
        for h in height:
            csum += h
            cent.append(round(100 * csum / users))

        x = [k * binsize for k in range(bins)]
        label = [f'{r} ({c})' for r,c in zip(x, cent)]

        l,r = 0,bins-1
        while not height[l]: l += 1
        while not height[r]: r -= 1
        x = x[l:r+1]
        cent = cent[l:r+1]
        label = label[l:r+1]
        colors = colors[l:r+1]
        height = height[l:r+1]

        plt.clf()
        fig = plt.figure(figsize=(15, 5))

        plt.xticks(rotation=45)
        plt.xlim(l * binsize - binsize//2, r * binsize + binsize//2)
        plt.bar(x, height, binsize*0.9, color=colors, linewidth=0, tick_label=label, log=(mode == 'log'))
        plt.xlabel('Rating')
        plt.ylabel('Number of users')

        discord_file = gc.get_current_figure_as_file()
        plt.close(fig)

        embed = discord_common.cf_color_embed(title=title)
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)



    @plot.command(brief='Show percentile distribution on codeforces', usage='[handles...] [+zoom] [+nomarker] [+exact]')
    async def centile(self, ctx, *args: str):
        """Show percentile distribution of codeforces and mark given handles in the plot. If +zoom and handles are given, it zooms to the neighborhood of the handles."""
        (zoom, nomarker, exact), args = cf_common.filter_flags(args, ['+zoom', '+nomarker', '+exact'])
        # Prepare data
        intervals = [(rank.low, rank.high) for rank in cf.RATED_RANKS]
        colors = [rank.color_graph for rank in cf.RATED_RANKS]

        ratings = cf_common.cache2.rating_changes_cache.get_all_ratings()
        ratings = np.array(sorted(ratings))
        n = len(ratings)
        perc = 100*np.arange(n)/n

        users_to_mark = {}
        if not nomarker:
            handles = args or ('!' + str(ctx.author),)
            handles = await cf_common.resolve_handles(ctx,
                                                      self.converter,
                                                      handles,
                                                      mincnt=0,
                                                      maxcnt=50)
            infos = await cf.user.info(handles=list(set(handles)))

            for info in infos:
                if info.rating is None:
                    raise GraphCogError(f'User `{info.handle}` is not rated')
                ix = bisect.bisect_left(ratings, info.rating)
                cent = 100*ix/len(ratings)
                users_to_mark[info.handle] = info.rating,cent

        # Plot
        plt.clf()
        fig,ax = plt.subplots(1)
        ax.plot(ratings, perc, color='#00000099')

        plt.xlabel('Rating')
        plt.ylabel('Percentile')

        for pos in ['right','top','bottom','left']:
            ax.spines[pos].set_visible(False)
        ax.tick_params(axis='both', which='both',length=0)

        # Color intervals by rank
        for interval,color in zip(intervals,colors):
            alpha = '99'
            l,r = interval
            col = color + alpha
            rect = patches.Rectangle((l,-50), r-l, 200,
                                     edgecolor='none',
                                     facecolor=col)
            ax.add_patch(rect)

        if users_to_mark:
            ymin = min(point[1] for point in users_to_mark.values())
            ymax = max(point[1] for point in users_to_mark.values())
            if zoom:
                ymargin = max(0.5, (ymax - ymin) * 0.1)
                ymin -= ymargin
                ymax += ymargin
            else:
                ymin = min(-1.5, ymin - 8)
                ymax = max(101.5, ymax + 8)
        else:
            ymin, ymax = -1.5, 101.5

        if users_to_mark and zoom:
            xmin = min(point[0] for point in users_to_mark.values())
            xmax = max(point[0] for point in users_to_mark.values())
            xmargin = max(20, (xmax - xmin) * 0.1)
            xmin -= xmargin
            xmax += xmargin
        else:
            xmin, xmax = ratings[0], ratings[-1]

        plt.xlim(xmin, xmax)
        plt.ylim(ymin, ymax)

        # Mark users in plot
        for user, point in users_to_mark.items():
            astr = f'{user} ({round(point[1], 2)})' if exact else user
            apos = ('left', 'top') if point[0] <= (xmax + xmin) // 2 else ('right', 'bottom')
            plt.annotate(astr,
                         xy=point,
                         xytext=(0, 0),
                         textcoords='offset points',
                         ha=apos[0],
                         va=apos[1])
            plt.plot(*point,
                     marker='o',
                     markersize=5,
                     color='red',
                     markeredgecolor='darkred')

        # Draw tick lines
        linecolor = '#00000022'
        inf = 10000
        def horz_line(y):
            l = mlines.Line2D([-inf,inf], [y,y], color=linecolor)
            ax.add_line(l)
        def vert_line(x):
            l = mlines.Line2D([x,x], [-inf,inf], color=linecolor)
            ax.add_line(l)
        for y in ax.get_yticks():
            horz_line(y)
        for x in ax.get_xticks():
            vert_line(x)

        # Discord stuff
        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title=f'Rating/percentile relationship')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)


    

    @discord_common.send_error_if(GraphCogError, cf_common.ResolveHandleError,
                                  cf_common.FilterError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(Graphs(bot))
