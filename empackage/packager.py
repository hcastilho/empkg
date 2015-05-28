"""
Base class to create project packagers
Projects should implement a class that inherits BasePackager and add any
extra required build/packaging steps
"""
import sys
from os import listdir
from os.path import join, exists, basename, isdir, isfile, abspath, dirname

from fabric.api import (
    settings,
    run,
    local,
    cd,
    env,
    sudo,
    put,
    shell_env,
    get,
    hide,
)
from fabric.contrib.files import upload_template
from fabtools.files import is_dir, is_file


BASE_PATH = "/tmp/"

class BasePackager(object):
    """
    Base class to create project packagers
    Projects should implement a class that inherits BasePackager and add any
    extra required build/packaging steps
    """
    def __init__(self, conf):
        conf['arch'] = conf.get('arch', 'all')
        conf['repo_type'] = conf.get('repo_type', 'git')
        conf['pkg_type'] = conf.get('pkg_type', 'deb')
        if not conf.get('pkg_paths'):
            conf['pkg_paths'] = [conf['prefix']]
        conf['hooks_dir'] = conf.get('hooks_dir', 'debian')
        conf['config_files'] = conf.get('config_files', [])
        conf['inplace'] = conf.get('inplace', False)
        if conf['inplace']:
            conf['src_path'] = conf.get('prefix')
        else:
            conf['src_path'] = join(BASE_PATH, conf['name'])

        # Temporary remote dir used to copy files and such
        conf['tmp_remote_dir'] = join(BASE_PATH, 'tmp_{}'.format(conf['name']))

        self.conf = conf

    def prepare(self):
        """Prepare system, install packages and fpm"""
        print 'Preparing...'
        # Prepare prefix dir
        if self.conf['prefix']:
            sudo('mkdir -p {}'.format(self.conf['prefix']))

        # Clean paths where our package will be installed
        if not self.conf.get('no_clean_pkg_paths'):
            if '/' in self.conf['pkg_paths']:
                print '/ in pkg_paths, you do not want to do that'
                sys.exit(1)
            sudo('rm -rf {}'.format(' '.join(self.conf['pkg_paths'])))

        # Clean temp dir
        sudo('rm -rf {}'.format(self.conf['tmp_remote_dir']))
        run('mkdir -p {}'.format(self.conf['tmp_remote_dir']))

        # Prepare project source
        if not self.conf.get('no_clean_checkout'):
            sudo('rm -rf {}'.format(self.conf['src_path']))
        run('mkdir -p {}'.format(self.conf['src_path']))
        sudo('chown -R {} {}'.format(env.user, self.conf['src_path']))
        if self.conf.get('src'):
            put('{}/*'.format(self.conf['src']), self.conf['src_path'])
        else:
            self.checkout_project()

    def build(self):
        """Packagers need to implement the build step themselves"""
        raise NotImplementedError

    def checkout_project(self):
        """Checkout/update project from repository"""
        # Setup deploy key
        self.setup_deploy_key()

        # TODO check if there is a repo on destination dir and change behaviour
        # to remove no_clean_checkout
        if self.conf.get('no_clean_checkout'):
            print 'Updating project...'
            if self.conf['repo_type'] == 'git':
                _git_update(self.conf['src_path'])
            elif self.conf['repo_type'] in ('hg', 'mercurial'):
                _hg_update(self.conf['src_path'], self.conf.get('commit', ''))
        else:
            print 'Checking out project...'
            if self.conf['repo_type'] == 'git':
                _git_clone(self.conf['repo'], self.conf['src_path'], self.conf.get('branch'))
            elif self.conf['repo_type'] in ('hg', 'mercurial'):
                _hg_clone(self.conf['repo'], self.conf['src_path'], self.conf.get('branch'))

        if self.conf['repo_type'] == 'git':
            self.conf['commit'] = _current_git_commit(self.conf['src_path'])
        elif self.conf['repo_type'] in ('hg', 'mercurial'):
            self.conf['commit'] = _current_hg_commit(self.conf['src_path'])

    def setup_deploy_key(self):
        """Setup deploy key"""
        if self.conf.get('deploy_key'):
            with settings(hide('running', 'warnings'), warn_only=True):
                if run('[ ! -d ~/.ssh ]').succeeded:
                    run('mkdir ~/.ssh')
            put(self.conf['deploy_key'], '~/.ssh/id_rsa', mirror_local_mode=True)

    def makepkg(self, push=False, download=True):
        """Build package"""
        print 'Building package...'

        with cd(self.conf['tmp_remote_dir']):
            self.copy_hooks()
            self.copy_changelog()
            cmd = self.get_fpm_cmd()
            fpm_output = sudo(cmd)
            deb_name = basename(fpm_output.split('"')[-2])

    def get_fpm_cmd(self):
        fpm_exec = 'fpm'
        cmd = (fpm_exec + ' '
            '-s dir '
            '-t {pkg_type} '
            '-n {name} '
            '-v {version} '
            '-a {arch} '
            '{vendor} '
            '{license} '
            '{maintainer} '
            '{homepage} '
            '--description "{description}" '
            '-x "**/*.bak" -x "**/*.orig" -x "**/.git*" -x "**/.hg*" '
            '{changelog} '
            '{hooks} '
            '{config_files} '
            '{deps} {paths}'
            .format(
                name=self.conf['name'],
                version=self.conf['version'],
                arch=self.conf.get('arch'),
                description=self.conf['description'],
                pkg_type=self.conf['pkg_type'],
                vendor=self.get_vendor_arg(),
                license=self.get_license_arg(),
                maintainer=self.get_maintainer_arg(),
                homepage=self.get_homepage_arg(),
                hooks=self.get_hooks_arg(),
                changelog=self.get_changelog_arg(),
                config_files=self.get_config_files_arg(),
                deps=self.get_dependencies_arg(),
                paths=' '.join(self.conf['pkg_paths']),
            ))
        return cmd

    def get_changelog_arg(self):
        changelog_file = join(self.conf['tmp_remote_dir'], 'changelog')
        if not is_file(changelog_file):
            return ''
        if self.conf['pkg_type'] == 'deb':
            arg = '--deb-changelog '
        elif self.conf['pkg_type'] == 'rpm':
            arg = '--rpm-changelog '
        return arg + changelog_file

    def copy_changelog(self):
        if 'changelog' in self.conf and isfile(self.conf.get('changelog')):
            chglog = abspath(self.conf['changelog'])
            upload_template(
                filename=basename(chglog),
                destination=join(self.conf['tmp_remote_dir'], 'changelog'),
                template_dir=dirname(chglog),
                context=self.get_context(),
                use_jinja=True)
        elif self.conf.get('changelog', False) == True:
            # TODO automatic changelog
            pass

    def get_vendor_arg(self):
        arg = ('--vendor {}'.format(self.conf['vendor'])
                if self.conf.get('vendor') else '')
        return arg

    def get_license_arg(self):
        arg = ('--license {}'.format(self.conf['license'])
                if self.conf.get('license') else '')
        return arg

    def get_maintainer_arg(self):
        arg = ('-m {}'.format(self.conf['maintainer'])
                if self.conf.get('maintainer') else '')
        return arg

    def get_homepage_arg(self):
        arg = ('--url {}'.format(self.conf['homepage'])
                if self.conf.get('homepage') else '')
        return arg

    def get_config_files_arg(self):
        if not self.conf['config_files']:
            return ''
        paths = (join(self.conf['prefix'], path) for path in self.conf['config_files'])
        return '--config-files ' + ' --config-files '.join(paths)

    def get_dependencies_arg(self):
        arg = ('-d "' + '" -d "'.join(self.conf['run_deps']) + '"'
                if self.conf.get('run_deps') else '')
        return arg


    def get_hooks_arg(self):
        hooks_str = ''
        if not isdir(self.conf['hooks_dir']):
            return ''
        hooks_str = ' '.join(
            '{} {}/{}'.format(opt, self.conf['tmp_remote_dir'], fname)
            for opt, fname in [
                ('--before-remove', 'prerm'),
                ('--after-remove', 'postrm'),
                ('--before-install', 'preinst'),
                ('--after-install', 'postinst'),
            ]
            if exists(join(self.conf['hooks_dir'], fname))
            )
        return hooks_str

    def copy_hooks(self):
        if not isdir(self.conf['hooks_dir']):
            return
        for fname in listdir(self.conf['hooks_dir']):
            upload_template(
                join(self.conf['hooks_dir'], fname),
                join(self.conf['tmp_remote_dir'], fname),
                self.get_context(),
                use_jinja=True)

    def get_context(self, extra_context=None):
        context = {
            'prefix': self.conf['prefix'],
        }
        context.update(self.conf)
        if extra_context:
            context.update(extra_context)
        return context


def _current_git_branch(src_path):
    with cd(src_path):
        return run('git symbolic-ref HEAD')[11:]


def _current_hg_branch(src_path):
    pass


def _current_git_commit(src_path):
    with cd(src_path):
        return run('git rev-parse --short HEAD')


def _current_hg_commit(src_path):
    pass


def _git_clone(repo, src_path, branch='master', commit=None):
    run('git clone {} -b {} {}'
        .format(repo, branch, src_path))
    if commit:
        run('git checkout {}'.format(commit))
    _git_update(src_path)


def _hg_clone(repo, src_path, branch='default', commit=None):
    cmd = 'hg clone {}'.format(repo)
    if branch:
        cmd = '{}#{}'.format(cmd, branch)
    cmd = '{} {}'.format(cmd, src_path)
    run(cmd)
    if commit:
        run('hg checkout {}'.format(commit))


def _git_update(src_path, remote='origin', branch='master'):
    with cd(src_path):
        run('git pull {} {}'.format(remote, branch))
        run('git submodule init')
        run('git submodule update')


def _hg_update(src_path, revision=''):
    with cd(src_path):
        run('hg pull')
        run('hg checkout {}'.format(revision))