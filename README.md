# Book Chatbot API

Cette application Python fournit une petite API REST ainsi qu’une interface
web minimaliste permettant de discuter avec un chatbot spécialisé dans
les livres.  Elle se base sur les APIs publiques d’[Open Library](https://openlibrary.org)
pour récupérer les métadonnées des ouvrages (titre, description,
auteurs, etc.).  Aucune clé API n’est nécessaire.

## Fonctionnalités

- **Recherche de livres :** envoie une requête à `https://openlibrary.org/search.json` et retourne une liste de résultats contenant le titre, le premier auteur et l’identifiant du *work*.  Les paramètres `q` et `limit` sont exposés.  La recherche est décrite dans la documentation d’Open Library ; l’API fournit un champ `q` pour la requête Solr et propose plusieurs options de tri et de filtrage【92707560968041†L90-L135】.
- **Détails d’un ouvrage :** à partir d’un identifiant de *work* (par exemple `OL27448W`), l’API appelle `https://openlibrary.org/works/{work_id}.json` et renvoie le titre, la description, les sujets et les identifiants des auteurs.  La description peut être une chaîne ou un objet avec un champ `value`.  L’Open Library distingue les *works* (regroupements d’éditions) des *editions* et expose des métadonnées détaillées par *work*【124795313457269†L115-L120】.
- **Chatbot :** reçoit un message et un `work_id` et tente de répondre :
  - Si le message contient les mots « résumé »/« summary »/« description », le bot retourne la description de l’ouvrage.  Le champ `description` provient du *work*【124795313457269†L115-L120】.
  - Si le message demande l’« auteur », les clés d’auteurs sont extraite, et un appel est fait à `https://openlibrary.org/{author_key}.json` pour récupérer le nom.  L’API de recherche renvoie les identifiants des auteurs dans le champ `author_key`【92707560968041†L174-L193】.
  - Si le message mentionne « pages », l’API récupère la première édition via `https://openlibrary.org/works/{work_id}/editions.json?limit=1` et renvoie le champ `number_of_pages`.
  - Sinon, le bot renvoie la description de l’ouvrage ou un message d’excuse.

L’interface web permet à l’utilisateur de rechercher un livre, de sélectionner un résultat et de poser des questions dans une conversation simple.  Le code JavaScript utilise l’API REST via `fetch`.

## Prérequis

- Python 3.9 ou supérieur.
- Les dépendances listées dans `requirements.txt` : FastAPI, Uvicorn, Requests, Jinja2, etc.  Installez-les avec :
  ```sh
  pip install -r requirements.txt
  ```

## Lancer le serveur

Exécutez la commande suivante à la racine du projet :

```sh
uvicorn app.main:app --reload
```

Puis ouvrez votre navigateur sur [http://localhost:8000](http://localhost:8000).  Une interface s’affiche pour rechercher un livre et discuter avec le bot.

## Architecture et conception

Le document des besoins fonctionnels et non fonctionnels fourni par l’utilisateur décrit une plateforme sociale pour les livres.  Parmi les fonctions prioritaires (MVP) figure un **chatbot IA spécialisé livres** qui permet à l’utilisateur de choisir un ouvrage et de poser des questions sur celui‑ci【997389085737852†L28-L34】.  Notre implémentation répond à ces besoins en proposant :

- un point d’entrée `/` affichant une interface simple et accessible ;
- un backend robuste qui interroge les APIs publiques d’Open Library pour constituer un socle de connaissances sans enfreindre le droit d’auteur (les descriptions sont limitées aux métadonnées disponibles)【997389085737852†L96-L97】 ;
- une logique de traitement des questions qui couvre les cas les plus courants (résumé, auteurs, nombre de pages) et renvoie un message par défaut sinon ;
- l’usage de FastAPI qui garantit une application rapide et facilement extensible, conformément aux exigences de simplicité, de rapidité et d’évolutivité énoncées dans les besoins non fonctionnels【997389085737852†L75-L116】.

Cette base peut être enrichie (authentification, salons privés, fil de posts, recommandations) pour s’intégrer dans la plateforme sociale complète décrite dans le document des besoins.