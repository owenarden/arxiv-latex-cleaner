
# Copyright 2018 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Cleans the LaTeX code of your paper to submit to arXiv."""
import collections
import os
import re
import shutil
import subprocess
import json

from PIL import Image

PDF_RESIZE_COMMAND = (
    'gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dNOPAUSE -dQUIET -dBATCH '
    '-dDownsampleColorImages=true -dColorImageResolution={resolution} '
    '-dColorImageDownsampleThreshold=1.0 -sOutputFile={output} {input}')
MAX_FILENAME_LENGTH = 120

# Fix for Windows: Even if '\' (os.sep) is the standard way of making paths on
# Windows, it interferes with regular expressions. We just change os.sep to '/'
# and os.path.join to a version using '/' as Windows will handle it the right
# way.
if os.name == 'nt':
  global old_os_path_join


  def new_os_join(path, *args):
    res = old_os_path_join(path, *args)
    res = res.replace('\\', '/')
    return res


  old_os_path_join = os.path.join

  os.sep = '/'
  os.path.join = new_os_join


def _create_dir_erase_if_exists(path):
  if os.path.exists(path):
    shutil.rmtree(path)
  os.makedirs(path)


def _create_dir_if_not_exists(path):
  if not os.path.exists(path):
    os.makedirs(path)


def _keep_pattern(haystack, patterns_to_keep):
  """Keeps the strings that match 'patterns_to_keep'."""
  out = []
  for item in haystack:
    if any((re.findall(rem, item) for rem in patterns_to_keep)):
      out.append(item)
  return out


def _remove_pattern(haystack, patterns_to_remove):
  """Removes the strings that match 'patterns_to_remove'."""
  return [
      item for item in haystack
      if item not in _keep_pattern(haystack, patterns_to_remove)
  ]


def _list_all_files(in_folder, ignore_dirs=None):
  if ignore_dirs is None:
    ignore_dirs = []
  to_consider = [
      os.path.join(os.path.relpath(path, in_folder), name)
      if path != in_folder else name
      for path, _, files in os.walk(in_folder)
      for name in files
  ]
  return _remove_pattern(to_consider, ignore_dirs)


def _copy_file(filename, params):
  _create_dir_if_not_exists(
      os.path.join(params['output_folder'], os.path.dirname(filename)))
  shutil.copy(
      os.path.join(params['input_folder'], filename),
      os.path.join(params['output_folder'], filename))


def _remove_command(text, command):
  """Removes '\\command{*}' from the string 'text'.

  Regex expression used to match balanced parentheses taken from:
  https://stackoverflow.com/questions/546433/regular-expression-to-match-balanced-parentheses/35271017#35271017
  """
  return re.sub(r'\\' + command + r'{(?:[^}{]+|{(?:[^}{]+|{[^}{]*})*})*}',
                '', text)


def _remove_environment(text, environment):
  """Removes '\\begin{environment}*\\end{environment}' from 'text'."""
  return re.sub(
      r'\\begin{' + environment + r'}[\s\S]*?\\end{' + environment + r'}',
      '', text)


def _remove_comments_inline(text):
  """Removes the comments from the string 'text'."""
  if 'auto-ignore' in text:
    return text
  if text.lstrip(' ').lstrip('\t').startswith('%'):
    return ''
  match = re.search(r'(?<!\\)%', text)
  if match:
    return text[:match.end()] + '\n'
  else:
    return text


def _read_file_content(filename):
  with open(filename, 'r') as fp:
    return fp.readlines()


def _read_all_tex_contents(tex_files, parameters):
  contents = {}
  for fn in tex_files:
    contents[fn] = _read_file_content(
        os.path.join(parameters['input_folder'], fn))
  return contents


def _write_file_content(content, filename):
  _create_dir_if_not_exists(os.path.dirname(filename))
  with open(filename, 'w') as fp:
    return fp.write(content)


def _remove_comments(content, parameters):
  """Erases all LaTeX comments in the content, and writes it."""
  content = [_remove_comments_inline(line) for line in content]
  content = _remove_environment(''.join(content), 'comment')
  for command in parameters['commands_to_delete']:
    content = _remove_command(content, command)
  # If file ends with '\n' already, the split in last line would add an extra
  # '\n', so we remove it.
  return content.split('\n')


def _resize_and_copy_figure(filename,
    origin_folder,
    destination_folder,
    resize_image,
    image_size,
    compress_pdf,
    pdf_resolution):
  """Resizes and copies the input figure (either JPG, PNG, or PDF)."""
  _create_dir_if_not_exists(
      os.path.join(destination_folder, os.path.dirname(filename)))

  if resize_image and os.path.splitext(filename)[1].lower() in ['.jpg',
                                                                '.jpeg',
                                                                '.png']:
    im = Image.open(os.path.join(origin_folder, filename))
    if max(im.size) > image_size:
      im = im.resize(
          tuple([int(x * float(image_size) / max(im.size)) for x in im.size]),
          Image.ANTIALIAS)
    if os.path.splitext(filename)[1].lower() in ['.jpg', '.jpeg']:
      im.save(os.path.join(destination_folder, filename), 'JPEG', quality=90)
    elif os.path.splitext(filename)[1].lower() in ['.png']:
      im.save(os.path.join(destination_folder, filename), 'PNG')

  elif compress_pdf and os.path.splitext(filename)[1].lower() == '.pdf':
    _resize_pdf_figure(filename, origin_folder, destination_folder,
                       pdf_resolution)
  else:
    shutil.copy(
        os.path.join(origin_folder, filename),
        os.path.join(destination_folder, filename))


def _resize_pdf_figure(filename, origin_folder, destination_folder, resolution,
    timeout=10):
  input_file = os.path.join(origin_folder, filename)
  output_file = os.path.join(destination_folder, filename)
  bash_command = PDF_RESIZE_COMMAND.format(input=input_file, output=output_file,
                                           resolution=resolution)
  process = subprocess.Popen(bash_command.split(), stdout=subprocess.PIPE)

  try:
    process.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
    process.kill()
    outs, errs = process.communicate()
    print('Output: ', outs)
    print('Errors: ', errs)


def _copy_only_referenced_non_tex_not_in_root(parameters, contents, splits):
  for fn in _keep_only_referenced(splits['non_tex_not_in_root'], contents):
    _copy_file(fn, parameters)


def _resize_and_copy_figures_if_referenced(parameters, contents, splits):
  image_size = collections.defaultdict(lambda: parameters['im_size'])
  image_size.update(parameters['images_whitelist'])
  pdf_resolution = collections.defaultdict(
      lambda: parameters['pdf_im_resolution'])
  pdf_resolution.update(parameters['images_whitelist'])
  for image_file in _keep_only_referenced(splits['figures'], contents):
    _resize_and_copy_figure(
        filename=image_file,
        origin_folder=parameters['input_folder'],
        destination_folder=parameters['output_folder'],
        resize_image=parameters['resize_images'],
        image_size=image_size[image_file],
        compress_pdf=parameters['compress_pdf'],
        pdf_resolution=pdf_resolution[image_file]
    )


def _keep_only_referenced(filenames, contents):
  """Returns the filenames referenced from contents."""
  return [fn for fn in filenames if os.path.splitext(fn)[0] in contents]


def _keep_only_referenced_tex(contents, splits):
  """Returns the filenames referenced from the tex files themselves

  It needs various iterations in case one file is referenced from an
  unreferenced file.
  """
  old_referenced = set(splits['tex_in_root'] + splits['tex_not_in_root'])
  while True:
    referenced = set(splits['tex_in_root'])
    for fn in old_referenced:
      for fn2 in old_referenced:
        if re.search(r'(' + os.path.splitext(fn)[0] + r'[.}])',
                     '\n'.join(contents[fn2])):
          referenced.add(fn)

    if referenced == old_referenced:
      splits['tex_to_copy'] = list(referenced)
      return

    old_referenced = referenced.copy()


def _add_root_tex_files(splits):
  # TODO: Check auto-ignore marker in root to detect the main file. Then check
  #  there is only one non-referenced TeX in root.

  # Forces the TeX in root to be copied, even if they are not referenced.
  for fn in splits['tex_in_root']:
    if fn not in splits['tex_to_copy']:
      splits['tex_to_copy'].append(fn)


def _split_all_files(parameters):
  """Splits the files into types or location to know what to do with them."""
  file_splits = {
      'all':
        _list_all_files(
            parameters['input_folder'], ignore_dirs=['.git' + os.sep]),
      'in_root': [
          f for f in os.listdir(parameters['input_folder'])
          if os.path.isfile(os.path.join(parameters['input_folder'], f))
      ]
  }

  file_splits['not_in_root'] = [
      f for f in file_splits['all'] if f not in file_splits['in_root']
  ]
  file_splits['to_copy_in_root'] = _remove_pattern(
      file_splits['in_root'],
      parameters['to_delete'] + parameters['figures_to_copy_if_referenced'])
  file_splits['to_copy_not_in_root'] = _remove_pattern(
      file_splits['not_in_root'],
      parameters['to_delete'] + parameters['figures_to_copy_if_referenced'])
  file_splits['figures'] = _keep_pattern(
      file_splits['all'], parameters['figures_to_copy_if_referenced'])

  file_splits['tex_in_root'] = _keep_pattern(file_splits['to_copy_in_root'],
                                             ['.tex$'])
  file_splits['tex_not_in_root'] = _keep_pattern(
      file_splits['to_copy_not_in_root'], ['.tex$'])

  file_splits['non_tex_in_root'] = _remove_pattern(
      file_splits['to_copy_in_root'], ['.tex$'])
  file_splits['non_tex_not_in_root'] = _remove_pattern(
      file_splits['to_copy_not_in_root'], ['.tex$'])

  return file_splits


def _create_out_folder(parameters):
  """Creates the output folder, erasing it if existed."""
  out_folder = (parameters['output_folder'] if parameters['output_folder'] is not None else ('arXiv_' + os.path.basename(parameters['input_folder'])))

  out_folder_base = (parameters['output_folder_base'] if parameters['output_folder_base'] is not None else os.getcwd())

  out_folder = os.path.join(out_folder_base, out_folder)
  _create_dir_erase_if_exists(out_folder)

  return out_folder

def _parse_config_file(cfgFilePath, parameters):
  cfgContent = _read_file_content(cfgFilePath)
  cfgJson = json.loads(''.join(cfgContent))
  parameters['config_parent_path'] = os.path.split(cfgFilePath)[0]
  parameters['input_folder'] = os.path.abspath(
    os.path.join(parameters['config_parent_path'], cfgJson['input_folder']))
  parameters['output_folder_base'] = os.path.abspath(
    os.path.join(parameters['config_parent_path'], cfgJson['output_folder_base']))

  if 'append_to_delete' in cfgJson:
    parameters['to_delete'] += cfgJson['append_to_delete']

  parameters.update({
    'output_folder' : cfgJson['output_folder'] if 'output_folder' in cfgJson else None,
    'append_file_to_copy' : cfgJson['append_file_to_copy'] if 'append_file_to_copy' in cfgJson else None,
    'append_folder_to_copy' : cfgJson['append_folder_to_copy'] if 'append_folder_to_copy' in cfgJson else None,
    'copied_file_keyword_replace' : cfgJson['copied_file_keyword_replace'] if 'copied_file_keyword_replace' in cfgJson else None,
    'pre_cmds' : cfgJson['pre_cmds'] if 'pre_cmds' in cfgJson else None,
    'post_cmds' : cfgJson['post_cmds'] if 'post_cmds' in cfgJson else None,
  })

def run_arxiv_cleaner(parameters):
  """Core of the code, runs the actual arXiv cleaner."""
  parameters.update({
      'to_delete': [
          '.aux$', '.sh$', '.bib$', '.blg$', '.brf$', '.log$', '.out$', '.ps$',
          '.dvi$', '.synctex.gz$', '~$', '.backup$', '.gitignore$',
          '.DS_Store$', '.svg$', '^.idea'
      ],
      'figures_to_copy_if_referenced': ['.png$', '.jpg$', '.jpeg$', '.pdf$'],
      'config_parent_path' : None,
      'output_folder' : None,
      'append_file_to_copy' : None,
      'append_folder_to_copy' : None,
      'copied_file_keyword_replace' : None,
      'pre_cmds' : None,
      'post_cmds' : None,
  })

  if parameters['input_folder'].endswith('.alccfg.json'):
    _parse_config_file(os.path.abspath(parameters['input_folder']), parameters)

  if parameters['pre_cmds'] is not None:
    with open('arXiv_latex_cleaner_pre_cmds.log', 'w') as logFile:
      for preCmd in parameters['pre_cmds']:
        wdPath = os.path.abspath(os.path.join(parameters['config_parent_path'], preCmd['cwd']))
        cmdEnv = preCmd['env']
        logFile.write('===============\n')
        logFile.write('ENV := ' + cmdEnv + '\n')
        logFile.write(wdPath + '\n')
        logFile.write(' '.join(preCmd['cmd']) + '\n')
        logFile.write('===============\n\n')
        logFile.flush()

        p = subprocess.Popen(preCmd['cmd'], cwd=wdPath, stdout=logFile, env=cmdEnv)
        p.wait()

        logFile.write('\n\n')
        logFile.flush()

  parameters['output_folder'] = _create_out_folder(parameters)

  splits = _split_all_files(parameters)

  tex_contents = _read_all_tex_contents(
      splits['tex_in_root'] + splits['tex_not_in_root'], parameters)

  for tex_file in tex_contents:
    tex_contents[tex_file] = _remove_comments(tex_contents[tex_file],
                                              parameters)

  _keep_only_referenced_tex(tex_contents, splits)
  _add_root_tex_files(splits)

  for tex_file in splits['tex_to_copy']:
    _write_file_content('\n'.join(tex_contents[tex_file]),
                        os.path.join(parameters['output_folder'], tex_file))

  full_content = '\n'.join(''.join(tex_contents[fn]) for fn in
                           splits['tex_to_copy'])
  _copy_only_referenced_non_tex_not_in_root(parameters, full_content, splits)
  for non_tex_file in splits['non_tex_in_root']:
    _copy_file(non_tex_file, parameters)

  _resize_and_copy_figures_if_referenced(parameters, full_content, splits)

  if parameters['append_file_to_copy'] is not None:
    for file in parameters['append_file_to_copy']:
      _copy_file(file, parameters)

  if parameters['append_folder_to_copy'] is not None:
    for folder in parameters['append_folder_to_copy']:
      for fileName in _list_all_files(os.path.join(parameters['input_folder'], folder)):
        if not any((re.findall(rem, fileName) for rem in parameters['to_delete'])):
          fileName = os.path.join(folder, fileName)
          _copy_file(fileName, parameters)

  if parameters['copied_file_keyword_replace'] is not None:
    for fileItem in parameters['copied_file_keyword_replace']:
      filePath = os.path.join(parameters['output_folder'], fileItem['filename'])
      fileContent = ''.join(_read_file_content(filePath))
      for matItem in fileItem['match_rules']:
        fileContent = fileContent.replace(matItem['key'], matItem['to'])
      _write_file_content(fileContent, filePath)

  if parameters['post_cmds'] is not None:
    with open('arXiv_latex_cleaner_post_cmds.log', 'w') as logFile:
      for postCmd in parameters['post_cmds']:
        wdPath = os.path.abspath(os.path.join(parameters['config_parent_path'], postCmd['cwd']))
        cmdEnv = postCmd['env']
        logFile.write('===============\n')
        logFile.write('ENV := ' + cmdEnv + '\n')
        logFile.write(wdPath + '\n')
        logFile.write(' '.join(postCmd['cmd']) + '\n')
        logFile.write('===============\n\n')
        logFile.flush()

        p = subprocess.Popen(postCmd['cmd'], cwd=wdPath, stdout=logFile, env=cmdEnv))
        p.wait()

        logFile.write('\n\n')
        logFile.flush()
