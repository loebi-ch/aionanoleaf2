from setuptools import setup

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="aionanoleaf2",
    version="1.0.3",
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
        "Documentation": "https://github.com/loebi-ch/aionanoleaf/blob/master/README.md",
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
    install_requires=["aiohttp"],
)
