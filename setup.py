# Copyright 2021, Milan Meulemans.
# Modified and optimized 2025-2026 by loebi-ch
# Added support for Nanoleaf Essentials based on the work of JaspervRijbroek in 2025
# Added support for 4D/Screen Mirroring emersion modes (1D, 2D, 3D, 4D) based on the work of jonathanrobichaud4 in 2024
# Added support for IPv6 hosts based on the work of krozgrov in 2025
#
# This file is part of aionanoleaf2, the refactored version of aionanoleaf by Milan Meulemans
#
# aionanoleaf2 is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# aionanoleaf2 is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with aionanoleaf2.  If not, see <https://www.gnu.org/licenses/>.

from setuptools import setup

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="aionanoleaf2",
    version="1.1.0",
    author="loebi-ch",
    author_email="andy@slyweb.ch",
    description="Async Python package for the Nanoleaf API that replaces aioNanoleaf.",
    keywords="nanoleaf api canvas shapes elements light panels strips essentials 4d emersion",
    license="LGPLv3+",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/loebi-ch/aionanoleaf2",
    project_urls={
        "Bug Tracker": "https://github.com/loebi-ch/aionanoleaf2/issues",
        "Source Code": "https://github.com/loebi-ch/aionanoleaf2",
        "Documentation": "https://github.com/loebi-ch/aionanoleaf2/blob/master/README.md",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Topic :: Home Automation",
        "Typing :: Typed",
    ],
    python_requires=">=3.8",
    packages=["aionanoleaf2"],
    package_data={"aionanoleaf2": ["py.typed"]},
    install_requires=["aiohttp"],
)
