# Domestic AI
Domestic AI is a local artificial intelligence environment crafted with 💛 by oio. It runs on your computer and can be accessible from anywhere.

## Philosophy
Domestic AI is a decentralised system made of small pieces of software cooperating together. It's modular and sustainable, and everybody can run it on their machine and customise it. AI is becoming a fundamental technology in many fields: we believe it should become accessible to everybody, and we want individuals to be able to build and own their own models. It's important for people to have control on the technologies they're using, not only to protect their data but also because learning how these tools work is empowering. For this reason we started to think about the concept of Domestic AI, and Roby is our first step to work on it. We're creating an open-source project containing instructions for the usage and contribution. We hope it will be useful to anybody wishing to experiment with AI without maybe having access to the commercial models. 

## What's Domestic AI made of 
Roby is a system of multiple independent apps running on your computer. The system is built in a semi-decentralised way so that if a piece breaks all the others aren't affected. You can call each service individually or use the Discord bot Roby or the Rest API interface, which can call all the others. Many apps generally rely on Ollama models, so you need to install it and have it running before being able to fully use the tools. By creating a tunnel from your domain to the apps, you can run your local AI from anywhere.

**🧠 Domestic API**
A REST API used to access all the Domestic AI features

**🤖 Roby**
A Discord bot that serves as an interface for the user to access the API. 

**🧰 Domestic Tools**
The set of tools accessible via the API. 

## How to work with this repo
The Domestic AI repository is a container of submodules. This means it collects and references to independent repositories. You can add new submodules and update existing ones. For new submodules it's recommended to create a new repo separately, and later add it to this as a submodule.
### Add a new submodule
> The standard case for adding a new submodule is adding a new tool for the Domestic AI universe. This must be done from the [Domestic Tools repo](https://github.com/oio/oio-domestic-tools). Go there and follow the instructions.

If you really want to add a new submodule here, please remember to discuss your decision with the owners of this repo. 
Here are the steps to follow to add a new submodule:
1. Create a new repository 
1. Document it
1. Push your code to it 
1. Move to this repo via ```cd [PATH_TO_YOUR_oio-domestic-ai]```
1. ```git submodule add [GITHUB_LINK_OF_THE_REPO_YOU_JUST_CREATED]```
1. ```git add .```
1. ```git commit -m 'added new submodule with name [NAME_OF_THE_SUBMODULE]'```
1. ```git push```

See [the submodules documentation](https://git-scm.com/book/en/v2/Git-Tools-Submodules) for more information.

### Update existing submodules or pool new tools
1. You can directly edit your changes from the submodule's folder inside the domestic-ai main repo
1. ```
git add .
git commit -m '[YOUR_COMMIT_MESSAGE]'
git push -u origin HEAD:main
```
1. After you pushed in the submodule's repo, you can just move to the domestic-ai main repo and ```git add .```, ```git commit -m 'edited submodule with name [NAME_OF_THE_SUBMODULE]'``` and finally ```git push```

## Used Ports
| Port Number | Usage |
|------------|-------|
| 11434 | Ollama |
| 8000 | Domestic API |
| 8800 | Domestic Tools |
| 8042 | Domestic Imagen |
| 8008 | Domestic Rembg |